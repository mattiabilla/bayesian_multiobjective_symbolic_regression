import warnings
from typing import Union

import numpy as np
import pandas as pd
import sympy as sym
from sympy.utilities.lambdify import lambdify

from symbolic_regression.Program import Program

warnings.filterwarnings("ignore")


def SGD(program: Program, data: Union[dict, pd.Series, pd.DataFrame], target: str, weights: str, constants_optimization_conf: dict, task: str):
    '''
    Stochastic Gradient Descent with analytic derivatives

    Args:
        - program: Program
            Program to be optimized
        - data: dict, pd.Series, pd.DataFrame
            Data to be used for optimization
        - target: str
            Name of the target column
        - weights: str
            Name of the weights column
        - constants_optimization_conf: dict
            Dictionary with the following
            - learning_rate: float
                Learning rate for the optimization
            - batch_size: int
                Batch size for the optimization
            - epochs: int
                Number of epochs for the optimization
            - gradient_clip: float
                Gradient clipping value
            - l1_param: float
                L1 regularization parameter
            - l2_param: float
                L2 regularization parameter
        - task: str
            Task to be performed. Can be 'regression' or 'classification'

    Returns:
        - list
            List of the optimized constants
        - list
            List of the loss values
        - list
            List of the accuracy values
    '''
    learning_rate = constants_optimization_conf['learning_rate']
    batch_size = constants_optimization_conf['batch_size']
    epochs = constants_optimization_conf['epochs']
    gradient_clip = constants_optimization_conf.get('gradient_clip', None)
    l1_param = constants_optimization_conf.get('l1_param', 0)
    l2_param = constants_optimization_conf.get('l2_param', 0)

    if not program.is_valid:  # No constants in program
        return [], [], []

    n_features = len(program.features)
    constants = np.array([item.feature for item in program.get_constants()])
    n_constants = constants.size

    if n_constants == 0:  # No constants in program
        return [], [], []

    # Initialize symbols for variables and constants
    x_sym = ''
    for f in program.features:
        x_sym += f'{f},'
    x_sym = sym.symbols(x_sym)
    c_sym = sym.symbols('c0:{}'.format(n_constants))

    # Initialize ground truth and data arrays
    y_true = np.reshape(data[target].to_numpy(), (data[target].shape[0], 1))
    X_data = data[program.features].to_numpy()
    if weights:
        w = np.reshape(data[weights].to_numpy(), (data[weights].shape[0], 1))
    else:
        w = np.ones_like(y_true)
    # convert program render into sympy formula (symplify?)
    p_sym = program.program.render(format_diff=True)

    # compute program analytic gradients with respect to the constants to be optimized
    grad = []
    for i in range(n_constants):
        grad.append(sym.diff(p_sym, f'c{i}'))

    # define gradient and program python functions from sympy object
    try:
        pyf_grad = lambdify([x_sym, c_sym], grad)
        pyf_prog = lambdify([x_sym, c_sym], p_sym)
    except KeyError:  # When the function doesn't have sense
        return [], [], []

    # Define batches
    n_batches = int(X_data.shape[0] / batch_size)
    X_batch = np.array_split(X_data, n_batches, 0)
    y_batch = np.array_split(y_true, n_batches, 0)
    w_batch = np.array_split(w, n_batches, 0)

    log, loss = [], []  # lists to store learning process

    # initialize variance
    var = 0.

    for _ in range(epochs):
        for i in range(n_batches):
            split_X_batch = np.split(X_batch[i], n_features, 1)
            split_c_batch = np.split(
                constants*np.ones_like(y_batch[i]), n_constants, 1)

            # Define current batch weights, and compute numerical values of pyf_grad pyf_prog
            y_pred = pyf_prog(tuple(split_X_batch), tuple(split_c_batch))
            num_grad = pyf_grad(tuple(split_X_batch), tuple(split_c_batch))

            if task == 'regression:wmse':
                av_loss = np.nanmean(w_batch[i] * (y_pred - y_batch[i])**2)
                av_grad = np.array([
                    np.nanmean(2. * w_batch[i] * (y_pred - y_batch[i]) * g)
                    for g in num_grad
                ])
            elif task == 'regression:wrrmse':
                y_av = np.mean(y_batch[i]*w_batch[i])+1e-20

                sq_term = np.sqrt(np.nanmean(
                    w_batch[i] * (y_pred - y_batch[i])**2))
                av_loss = sq_term*100./y_av
                av_grad = np.array(
                    [100./(y_av*sq_term) * np.nanmean(w_batch[i] * (y_pred - y_batch[i]) * g) for g in num_grad])

            elif task == 'binary:logistic':
                # compute average loss
                # w=np.where(y_batch[i]==1, 1./(2*y_batch[i].mean()),  1./(2*(1-y_batch[i].mean())))
                # av_loss=np.nanmean(-w*y_batch[i]*np.log(y_pred+1e-20)-w*(1.-y_batch[i])*np.log(1.-y_pred+1e-20))
                sigma = 1. / (1. + np.exp(-y_pred)
                              )  # numerical value of sigmoid(program)
                av_loss = np.nanmean(
                    -w_batch[i] *
                    (y_batch[i] * np.log(sigma + 1e-20) +
                     (1. - y_batch[i]) * np.log(1. - sigma + 1e-20)))
                # compute average gradients
                av_grad = np.array([
                    np.nanmean(w_batch[i] * (sigma - y_batch[i]) * g)
                    for g in num_grad
                ])

            # try with new constants if loss is nan
            if np.isnan(av_loss):
                var += 0.2
                constants = np.random.normal(0.0, var, constants.shape)

            norm_grad = np.linalg.norm(av_grad)
            if gradient_clip and (norm_grad > 1.):  # normalize gradients
                av_grad = av_grad / (norm_grad + 1e-20)

            # Updating constants
            constants -= learning_rate * av_grad + 2 * learning_rate * l2_param * \
                constants + learning_rate * l1_param * np.sign(constants)

        log.append(list(constants))
        loss.append(av_loss)

    return constants, loss, log


def ADAM(program: Program, data: Union[dict, pd.Series, pd.DataFrame], target: str, weights: str, constants_optimization_conf: dict, task: str):
    ''' ADAM with analytic derivatives

    Args:
        - program: Program
            The program to optimize
        - data: dict, pd.Series, pd.DataFrame
            The data to fit the program
        - target: str
            The target column name
        - weights: str
            The weights column name
        - constants_optimization_conf: dict
            Dictionary with the following
            - learning_rate: float
                The learning rate
            - batch_size: int
                The batch size
            - epochs: int
                The number of epochs
            - gradient_clip: bool
                Whether to clip the gradients
            - beta_1: float
                The beta 1 parameter for ADAM
            - beta_2: float
                The beta 2 parameter for ADAM
            - epsilon: float
                The epsilon parameter for ADAM
            - l1_param: float
                The l1 regularization parameter
            - l2_param: float
                The l2 regularization parameter
            
        - task: str
            The task to optimize

    Returns:
        - constants: np.array
            The optimized constants
        - loss: list
            The loss at each epoch
        - log: list
            The constants at each epoch
    '''

    learning_rate = constants_optimization_conf['learning_rate']
    batch_size = constants_optimization_conf['batch_size']
    epochs = constants_optimization_conf['epochs']
    gradient_clip = constants_optimization_conf['gradient_clip']
    beta_1 = constants_optimization_conf['beta_1']
    beta_2 = constants_optimization_conf['beta_2']
    epsilon = constants_optimization_conf['epsilon']
    l1_param = constants_optimization_conf.get('l1_param', 0)
    l2_param = constants_optimization_conf.get('l2_param', 0)

    if not program.is_valid:  # No constants in program
        return [], [], []

    n_features = len(program.features)
    constants = np.array([item.feature for item in program.get_constants()])
    n_constants = constants.size

    if n_constants == 0:  # No constants in program
        return [], [], []

    # Initialize symbols for variables and constants
    x_sym = ''
    for f in program.features:
        x_sym += f'{f},'
    x_sym = sym.symbols(x_sym)
    c_sym = sym.symbols('c0:{}'.format(n_constants))

    # Initialize ground truth and data arrays
    y_true = np.reshape(data[target].to_numpy(), (data[target].shape[0], 1))
    X_data = data[program.features].to_numpy()
    if weights:
        w = np.reshape(data[weights].to_numpy(), (data[weights].shape[0], 1))
    else:
        w = np.ones_like(y_true)

    # convert program render into sympy formula (symplify?)
    p_sym = program.program.render(format_diff=True)

    # compute program analytic gradients with respect to the constants to be optimized
    grad = []
    for i in range(n_constants):
        grad.append(sym.diff(p_sym, f'c{i}'))

    # define gradient and program python functions from sympy object

    try:
        pyf_grad = lambdify([x_sym, c_sym], grad)
        pyf_prog = lambdify([x_sym, c_sym], p_sym)
    except KeyError:  # When the function doesn't have sense
        return [], [], []
    # Define batches
    n_batches = int(X_data.shape[0] / batch_size)
    X_batch = np.array_split(X_data, n_batches, 0)
    y_batch = np.array_split(y_true, n_batches, 0)
    w_batch = np.array_split(w, n_batches, 0)

    log, loss = [], []  # lists to store learning process

    # Initialize Adam variables
    m = 0
    v = 0
    t = 1
    var = 0

    for _ in range(epochs):
        for i in range(n_batches):

            split_X_batch = np.split(X_batch[i], n_features, 1)
            split_c_batch = np.split(
                constants*np.ones_like(y_batch[i]), n_constants, 1)

            # Define current batch weights, and compute numerical values of pyf_grad pyf_prog
            y_pred = pyf_prog(tuple(split_X_batch), tuple(split_c_batch))
            num_grad = pyf_grad(tuple(split_X_batch), tuple(split_c_batch))

            if task == 'regression:wmse':
                av_loss = np.nanmean(w_batch[i] * (y_pred - y_batch[i])**2)
                av_grad = np.array([
                    np.nanmean(2 * w_batch[i] * (y_pred - y_batch[i]) * g)
                    for g in num_grad
                ])

            elif task == 'regression:wrrmse':
                y_av = np.mean(y_batch[i]*w_batch[i])+1e-20

                sq_term = np.sqrt(np.nanmean(
                    w_batch[i] * (y_pred - y_batch[i])**2))
                av_loss = sq_term*100./y_av
                av_grad = np.array(
                    [100./(y_av*sq_term) * np.nanmean(w_batch[i] * (y_pred - y_batch[i]) * g) for g in num_grad])

            elif task == 'binary:logistic':
                # compute average loss
                #w=np.where(y_batch[i]==1, 1./(2*y_batch[i].mean()),  1./(2*(1-y_batch[i].mean())))
                # av_loss=np.nanmean(-w*y_batch[i]*np.log(y_pred+1e-20)-w*(1.-y_batch[i])*np.log(1.-y_pred+1e-20))
                sigma = 1. / (1. + np.exp(-y_pred)
                              )  # numerical value of sigmoid(program)
                av_loss = np.nanmean(
                    -w_batch[i] *
                    (y_batch[i] * np.log(sigma + 1e-20) +
                     (1. - y_batch[i]) * np.log(1. - sigma + 1e-20)))
                # compute average gradients
                av_grad = np.array([
                    np.nanmean(w_batch[i] * (sigma - y_batch[i]) * g)
                    for g in num_grad
                ])

            # try with new constants if loss is nan
            if np.isnan(av_loss):
                var += 0.2
                constants = np.random.normal(0.0, var, constants.shape)

            norm_grad = np.linalg.norm(av_grad)
            if gradient_clip and (norm_grad > 1.):  # normalize gradients
                av_grad = av_grad / (norm_grad + 1e-20)

            # Updating momentum variables
            m = beta_1 * m + (1 - beta_1) * av_grad
            v = beta_2 * v + (1 - beta_2) * np.power(av_grad, 2)
            m_hat = m / (1 - np.power(beta_1, t))
            v_hat = v / (1 - np.power(beta_2, t))
            t += 1

            # Update constants
            constants -= learning_rate * m_hat / \
                (np.sqrt(v_hat) + epsilon) + 2 * learning_rate * l2_param * \
                constants + learning_rate * l1_param * np.sign(constants)

        log.append(list(constants))
        loss.append(av_loss)

    return constants, loss, log


def ADAM2FOLD(program: Program, data: Union[dict, pd.Series, pd.DataFrame], target: list, weights: list, constants_optimization_conf: dict, task: str):
    ''' ADAM with analytic derivatives for 2-fold programs

    Args:
        -program: Program
            The program to optimize
        -data: dict, pd.Series, pd.DataFrame
            The data to fit
        -target: list
            The targets to fit
        -weights: list
            The weights to fit
        -constants_optimization_conf: dict
            Dictionary with the following
            - learning_rate: float
                The learning rate
            - batch_size: int
                The batch size
            - epochs: int
                The number of epochs
            - gradient_clip: bool
                Whether to clip the gradients
            - beta_1: float
                The beta_1 parameter for Adam
            - beta_2: float
                The beta_2 parameter for Adam
            - epsilon: float
                The epsilon parameter for Adam
        -task: str
            The task to optimize

    Returns:
        -constants: list
            The optimized constants
        -loss: list
            The loss at each epoch
        -log: list
            The constants at each epoch
    '''
    #print('using ADAM2FOLD')
    learning_rate = constants_optimization_conf['learning_rate']
    batch_size = constants_optimization_conf['batch_size']
    epochs = constants_optimization_conf['epochs']
    gradient_clip = constants_optimization_conf['gradient_clip']
    beta_1 = constants_optimization_conf['beta_1']
    beta_2 = constants_optimization_conf['beta_2']
    epsilon = constants_optimization_conf['epsilon']

    if not program.is_valid:  # No constants in program
        return [], [], []

    n_features = len(program.features)
    constants = np.array([item.feature for item in program.get_constants()])
    n_constants = constants.size

    if n_constants == 0:  # No constants in program
        return [], [], []

    # Initialize symbols for variables and constants
    x_sym = ''
    for f in program.features:
        x_sym += f'{f},'
    x_sym = sym.symbols(x_sym)
    c_sym = sym.symbols('c0:{}'.format(n_constants))

    # Initialize ground truth and data arrays
    y_true_1 = np.reshape(data[target[0]].to_numpy(),
                          (data[target[0]].shape[0], 1))
    y_true_2 = np.reshape(data[target[1]].to_numpy(),
                          (data[target[1]].shape[0], 1))
    X_data = data[program.features].to_numpy()
    if weights:
        w1 = np.reshape(data[weights[0]].to_numpy(),
                        (data[weights[0]].shape[0], 1))
        w2 = np.reshape(data[weights[1]].to_numpy(),
                        (data[weights[1]].shape[0], 1))
    else:
        w1 = np.ones_like(y_true_1)
        w2 = np.ones_like(y_true_2)

    # convert program render into sympy formula (symplify?)
    p_sym = program.program.render(format_diff=True)

    # compute program analytic gradients with respect to the constants to be optimized
    grad = []
    for i in range(n_constants):
        grad.append(sym.diff(p_sym, f'c{i}'))

    # define gradient and program python functions from sympy object

    try:
        pyf_grad = lambdify([x_sym, c_sym], grad)
        pyf_prog = lambdify([x_sym, c_sym], p_sym)
    except KeyError:  # When the function doesn't have sense
        return [], [], []
    # Define batches
    n_batches = int(X_data.shape[0] / batch_size)
    X_batch = np.array_split(X_data, n_batches, 0)
    y1_batch = np.array_split(y_true_1, n_batches, 0)
    y2_batch = np.array_split(y_true_2, n_batches, 0)
    w1_batch = np.array_split(w1, n_batches, 0)
    w2_batch = np.array_split(w2, n_batches, 0)

    log, loss = [], []  # lists to store learning process

    # Initialize Adam variables
    m = 0
    v = 0
    t = 1
    var = 0

    samples = 100

    for _ in range(epochs):
        for i in range(n_batches):
            # sample lambdas from distribution
            lambda1 = np.random.uniform(low=0.0, high=1.0, size=(1, samples))

            split_X_batch = np.split(X_batch[i], n_features, 1)
            split_c_batch = np.split(
                constants*np.ones_like(y1_batch[i]), n_constants, 1)

            # Define current batch weights, and compute numerical values of pyf_grad pyf_prog
            y_pred = pyf_prog(tuple(split_X_batch), tuple(split_c_batch))
            num_grad = pyf_grad(tuple(split_X_batch), tuple(split_c_batch))

            if task == 'regression:wmse':  # (N,1)
                av_loss = np.nanmean(lambda1*(w1_batch[i] * (y_pred - y1_batch[i])**2)
                                     + (1-lambda1)*(w2_batch[i] * (y_pred - y2_batch[i])**2))
                av_grad = np.array([
                    np.nanmean(2 * (lambda1*(w1_batch[i](y_pred - y1_batch[i]))
                                   + (1-lambda1)*(w2_batch[i](y_pred - y2_batch[i]))) * g)
                    for g in num_grad
                ])

            # try with new constants if loss is nan
            if np.isnan(av_loss):
                var += 0.2
                constants = np.random.normal(0.0, var, constants.shape)

            norm_grad = np.linalg.norm(av_grad)
            if gradient_clip and (norm_grad > 1.):  # normalize gradients
                av_grad = av_grad / (norm_grad + 1e-20)

            # Updating momentum variables
            m = beta_1 * m + (1 - beta_1) * av_grad
            v = beta_2 * v + (1 - beta_2) * np.power(av_grad, 2)
            m_hat = m / (1 - np.power(beta_1, t))
            v_hat = v / (1 - np.power(beta_2, t))
            t += 1

            # Update constants
            constants -= learning_rate * m_hat / (np.sqrt(v_hat) + epsilon)

        log.append(list(constants))
        loss.append(av_loss)

    return constants, loss, log
