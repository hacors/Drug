"""Utilities for pytorch NN package"""
#pylint: disable=no-member, invalid-name

import torch as th
from torch import nn


def matmul_maybe_select(A, B):
    """Perform Matrix multiplication C = A * B but A could be an integer id vector.

    If A is an integer vector, we treat it as multiplying a one-hot encoded tensor.
    In this case, the expensive dense matrix multiply can be replaced by a much
    cheaper index lookup.

    For example,
    ::

        A = [2, 0, 1],
        B = [[0.1, 0.2],
             [0.3, 0.4],
             [0.5, 0.6]]

    then matmul_maybe_select(A, B) is equivalent to
    ::

        [[0, 0, 1],     [[0.1, 0.2],
         [1, 0, 0],  *   [0.3, 0.4],
         [0, 1, 0]]      [0.5, 0.6]]

    In all other cases, perform a normal matmul.

    Parameters
    ----------
    A : torch.Tensor
        lhs tensor
    B : torch.Tensor
        rhs tensor

    Returns
    -------
    C : torch.Tensor
        result tensor
    """
    if A.dtype == th.int64 and len(A.shape) == 1:
        return B.index_select(0, A)
    else:
        return th.matmul(A, B)

def bmm_maybe_select(A, B, index):
    """Slice submatrices of A by the given index and perform bmm.

    B is a 3D tensor of shape (N, D1, D2), which can be viewed as a stack of
    N matrices of shape (D1, D2). The input index is an integer vector of length M.
    A could be either:
    (1) a dense tensor of shape (M, D1),
    (2) an integer vector of length M.
    The result C is a 2D matrix of shape (M, D2)

    For case (1), C is computed by bmm:
    ::

        C[i, :] = matmul(A[i, :], B[index[i], :, :])

    For case (2), C is computed by index select:
    ::

        C[i, :] = B[index[i], A[i], :]

    Parameters
    ----------
    A : torch.Tensor
        lhs tensor
    B : torch.Tensor
        rhs tensor
    index : torch.Tensor
        index tensor

    Returns
    -------
    C : torch.Tensor
        return tensor
    """
    if A.dtype == th.int64 and len(A.shape) == 1:
        # following is a faster version of B[index, A, :]
        B = B.view(-1, B.shape[2])
        flatidx = index * B.shape[1] + A
        return B.index_select(0, flatidx)
    else:
        BB = B.index_select(0, index)
        return th.bmm(A.unsqueeze(1), BB).squeeze()

# pylint: disable=W0235
class Identity(nn.Module):
    """A placeholder identity operator that is argument-insensitive.
    (Identity has already been supported by PyTorch 1.2, we will directly
    import torch.nn.Identity in the future)
    """
    def __init__(self):
        super(Identity, self).__init__()

    def forward(self, x):
        """Return input"""
        return x
