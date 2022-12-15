import concurrent.futures
import logging
import os
import time
from typing import Union

import numpy as np
import pandas as pd
import pygmo as pg
from joblib.parallel import Parallel, delayed

from symbolic_regression.multiobjective.training import (create_pareto_front,
                                                         crowding_distance,
                                                         generate_population,
                                                         get_offspring)

from loky import get_reusable_executor

backend_parallel = 'loky'


class SymbolicRegressor:
    def __init__(
        self,
        checkpoint_file: str = None,
        checkpoint_frequency: int = -1,
        const_range: tuple = None,
        parsimony=0.9,
        parsimony_decay=0.9,
        population_size: int = 100,
        tournament_size: int = 10,
    ) -> None:
        """ This class implements the basic features for training a Symbolic Regression algorithm

        Args:
            - const_range: this is the range of values from which to generate constants in the program
            - fitness_functions: the functions to use for evaluating programs' performance
            - parsimony: the ratio to which a new operation is chosen instead of a terminal node in program generations
            - parsimony_decay: a modulation parameter to decrease the parsimony and limit program generation depth
            - tournament_size: this modulate the tournament selection and set the dimension of the selection
        """

        # Model characteristics
        self.best_program = None
        self.best_programs_history = []
        self.first_pareto_front_history = []
        self.converged_generation = None
        self.generation = None
        self.population = None
        self.population_size = population_size
        self.status = "Uninitialized"
        self.training_duration = None
        self.fpf_hypervolume = None
        self.fpf_hypervolume_history = []

        # Training configurations
        self.checkpoint_file = checkpoint_file
        self.checkpoint_frequency = checkpoint_frequency
        self.const_range = const_range
        self.parsimony = parsimony
        self.parsimony_decay = parsimony_decay
        self.tournament_size = tournament_size
        self.elapsed_time = 0

        # Population characteristics
        self.average_complexity = None

    def drop_duplicates(self, inplace: bool = False) -> list:
        """ This method removes duplicated programs

        Programs are considered duplicated if they have the same performance

        Args:
            - inplace: allow to overwrite the current population or duplicate the object
        """

        for index, p in enumerate(self.population):
            if p.is_valid and not p._is_duplicated:
                for p_confront in self.population[index + 1:]:
                    if p.is_duplicate(p_confront):
                        p_confront._is_duplicated = True  # Makes p.is_valid = False

        if inplace:
            self.population = list(
                filter(lambda p: p._is_duplicated == False, self.population))
            return self.population

        return list(
            filter(lambda p: p._is_duplicated == False, self.population))

    def drop_invalids(self, inplace: bool = False) -> list:
        """ This program removes invalid programs from the population

        A program can be invalid when mathematical operation are not possible
        or if the siplification generated operation which are not supported.

        Args:
            - inplace: allow to overwrite the current population or duplicate the object
        """
        if inplace:
            self.population = list(
                filter(lambda p: p.is_valid == True, self.population))
            return self.population

        return list(filter(lambda p: p.is_valid == True, self.population))

    def fit(self,
            data: Union[dict, pd.Series, pd.DataFrame],
            features: list,
            fitness_functions: dict,
            generations: int,
            genetic_operators_frequency: dict,
            operations: list,
            n_jobs: int = -1,
            stop_at_convergence: bool = True,
            verbose: int = 0):
        """This method support a KeyboardInterruption of the fit process

        This allow to interrupt the training at any point without losing
        the progress made.
        """
        if not self.generation:
            self.generation = 0
        start = time.perf_counter()
        try:
            self._fit(data=data,
                      features=features,
                      fitness_functions=fitness_functions,
                      generations=generations,
                      genetic_operators_frequency=genetic_operators_frequency,
                      operations=operations,
                      n_jobs=n_jobs,
                      stop_at_convergence=stop_at_convergence,
                      verbose=verbose)
        except KeyboardInterrupt:
            self.generation -= 1  # The increment is applied even if the generation is interrupted
            stop = time.perf_counter()
            self.training_duration = stop - start
            self.status = "Interrupted by KeyboardInterrupt"
            logging.warning(f"Training terminated by a KeyboardInterrupt")
            return
        stop = time.perf_counter()
        self.training_duration = stop - start

    def _fit(self,
             data: Union[dict, pd.Series, pd.DataFrame],
             features: list,
             fitness_functions: dict,
             generations: int,
             genetic_operators_frequency: dict,
             operations: list,
             n_jobs: int = -1,
             stop_at_convergence: bool = True,
             verbose: int = 0) -> list:

        if not self.population:
            logging.info(f"Initializing population")
            self.status = "Generating population"
            self.population = Parallel(
                n_jobs=n_jobs,
                backend=backend_parallel)(delayed(generate_population)(
                    data=data,
                    features=features,
                    const_range=self.const_range,
                    operations=operations,
                    fitness=fitness_functions,
                    parsimony=self.parsimony,
                    parsimony_decay=self.parsimony_decay,
                ) for _ in range(self.population_size))

        else:
            logging.info("Fitting with existing population")

        while True:
            if generations > 0 and self.generation >= generations:
                logging.info(
                    f"The model already had trained for {self.generation} generations")
                self.status = "Terminated: generations completed"
                return

            self.generation += 1

            start_time_generation = time.perf_counter()
            converged_time = None

            if self.generation > 1:
                seconds_iter = round(self.elapsed_time /
                                     (self.generation-1), 1)
                timing_str = f"{self.elapsed_time} sec, {seconds_iter} sec/generation"
            else:
                timing_str = f"{self.elapsed_time} sec"

            if verbose > 0:
                print("############################################################")
                print(
                    f"Generation {self.generation}/{generations} - {timing_str}")
            else:
                print(
                    f"Generation {self.generation}/{generations} - {timing_str}", end='\r')

            logging.debug(f"Generating offspring")
            self.status = "Generating offspring"

            offsprings = []

            m_workers = n_jobs if n_jobs > 0 else os.cpu_count()

            executor = get_reusable_executor(max_workers=m_workers)
            offsprings = list(set(executor.map(
                get_offspring, timeout=120,
                initargs=(
                    self.population,
                    data,
                    fitness_functions,
                    self.generation,
                    self.tournament_size,
                    genetic_operators_frequency
                ))))
            
            self.population += offsprings

            # Removes all non valid programs in the population
            logging.debug(f"Removing duplicates")
            before_cleaning = len(self.population)

            self.drop_duplicates(inplace=True)

            after_drop_duplicates = len(self.population)
            logging.debug(
                f"{before_cleaning-after_drop_duplicates}/{before_cleaning} duplicates programs removed"
            )

            self.drop_invalids(inplace=True)

            after_cleaning = len(self.population)
            if before_cleaning != after_cleaning:
                logging.debug(
                    f"{after_drop_duplicates-after_cleaning}/{after_drop_duplicates} invalid programs removed"
                )

            # Integrate population in case of too many invalid programs
            if len(self.population) < self.population_size * 2:
                self.status = "Refilling population"
                missing_elements = 2*self.population_size - \
                    len(self.population)

                logging.info(
                    f"Population of {len(self.population)} elements is less than 2*population_size:{self.population_size*2}. Integrating with {missing_elements} new elements"
                )

                refill = Parallel(
                    n_jobs=n_jobs, batch_size=28,
                    backend=backend_parallel)(delayed(generate_population)(
                        data=data,
                        features=features,
                        const_range=self.const_range,
                        operations=operations,
                        fitness=fitness_functions,
                        parsimony=self.parsimony,
                        parsimony_decay=self.parsimony_decay,
                    ) for _ in range(missing_elements))

                self.population += refill

            logging.debug(f"Creating pareto front")
            self.status = "Creating pareto front"
            create_pareto_front(self.population)

            logging.debug(f"Creating crowding distance")
            self.status = "Creating crowding distance"
            crowding_distance(self.population)

            self.population.sort(key=lambda p: p.crowding_distance,
                                 reverse=True)
            self.population.sort(key=lambda p: p.rank, reverse=False)
            self.population = self.population[:self.population_size]

            self.best_program = self.population[0]
            self.best_programs_history.append(self.best_program)
            self.first_pareto_front_history.append(list(self.first_pareto_front))

            self.average_complexity = np.mean(
                [p.complexity for p in self.population])

            if verbose > 1:
                print()
                print(
                    f"Population of {len(self.population)} elements and average complexity of {self.average_complexity} and 1PF hypervolume of {self.hypervolume}\n"
                )
                print(
                    f"\tBest individual(s) in the first Pareto Front"
                )
                first_p_printed = 0
                for p in self.population:
                    if p.rank > 1:
                        continue
                    print(f'{first_p_printed})\t{p.program}')
                    print()
                    print(f'\t{p.fitness}')
                    print()
                    first_p_printed += 1

            if verbose > 2:
                try:
                    print(f"Following 5 best fitness")
                    print(
                        f"{first_p_printed})\t{self.population[first_p_printed+1].fitness}")
                    print(
                        f"{first_p_printed})\t{self.population[first_p_printed+2].fitness}")
                    print(
                        f"{first_p_printed})\t{self.population[first_p_printed+3].fitness}")
                    print(
                        f"{first_p_printed})\t{self.population[first_p_printed+4].fitness}")
                    print('...\t...\n')

                except IndexError:
                    pass  # Stops printing in very small populations

            end_time_generation = time.perf_counter()
            
            if self.best_program.converged:
                converged_time = time.perf_counter()
                if not self.converged_generation:
                    self.converged_generation = self.generation
                logging.info(
                    f"Training converged after {self.converged_generation} generations."
                )
                if stop_at_convergence:
                    self.status = "Terminated: converged"
                    return

            if self.checkpoint_file and self.checkpoint_frequency > 0 and self.generation % self.checkpoint_frequency == 0:
                try:
                    self.save_model(file=self.checkpoint_file)
                except FileNotFoundError:
                    logging.warning(
                        f'FileNotFoundError raised in checkpoint saving')

            # Use generations = -1 to rely only on convergence (risk of infinite loop)
            if generations > 0 and self.generation == generations:
                logging.info(
                    f"Training terminated after {self.generation} generations")
                self.status = "Terminated: generations completed"
                return

            self.elapsed_time += end_time_generation - start_time_generation

    @property
    def hypervolume(self):
        
        fitness_to_hypervolume = []
        for fitness, value in self.first_pareto_front[0]._fitness_template.items():
            if value.get('hv_reference', None) and value.get('minimize', True):
                fitness_to_hypervolume.append(fitness)

        references = [self.first_pareto_front[0]._fitness_template[fitness]['hv_reference'] for fitness in fitness_to_hypervolume]
        points = [[p._fitness_template[fitness]['func'] for fitness in fitness_to_hypervolume] for p in self.first_pareto_front]

        try:
            self.fpf_hypervolume = pg.hypervolume(points).compute(references)
            self.fpf_hypervolume_history.append(self.fpf_hypervolume)
        except:
            self.fpf_hypervolume = 0
            self.fpf_hypervolume_history.append(0)
            
        return self.fpf_hypervolume

    def save_model(self, file: str):
        import pickle

        with open(file, "wb") as f:
            pickle.dump(self, f)

    def load_model(self, file: str):
        import pickle

        with open(file, "rb") as f:
            return pickle.load(f)
    
    @property
    def first_pareto_front(self):
        return [p for p in self.population if p.rank == 1]

    @property
    def summary(self):
        istances = []

        for index, p in enumerate(self.population):
            row = {}
            row['index'] = index + 1
            row['program'] = p.program
            row['complexity'] = p.complexity
            row['rank'] = p.rank

            for f_k, f_v in p.fitness.items():
                row[f_k] = f_v

            istances.append(row)

        return pd.DataFrame(istances)

    @property
    def best_history(self):
        istances = []

        for index, p in enumerate(self.best_programs_history):
            row = {}
            row['generation'] = index + 1
            row['program'] = p.program
            row['complexity'] = p.complexity
            row['rank'] = p.rank

            for f_k, f_v in p.fitness.items():
                row[f_k] = f_v

            istances.append(row)

        return pd.DataFrame(istances)
