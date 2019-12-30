"""Torch Module for DenseChebConv"""
# pylint: disable= no-member, arguments-differ, invalid-name
import torch as th
from torch import nn
from torch.nn import init


class DenseChebConv(nn.Module):
    r"""Chebyshev Spectral Graph Convolution layer from paper `Convolutional
    Neural Networks on Graphs with Fast Localized Spectral Filtering
    <https://arxiv.org/pdf/1606.09375.pdf>`__.

    We recommend to use this module when inducing ChebConv operations on dense
    graphs / k-hop graphs.

    Parameters
    ----------
    in_feats: int
        Number of input features.
    out_feats: int
        Number of output features.
    k : int
        Chebyshev filter size.
    bias : bool, optional
        If True, adds a learnable bias to the output. Default: ``True``.

    See also
    --------
    ChebConv
    """
    def __init__(self,
                 in_feats,
                 out_feats,
                 k,
                 bias=True):
        super(DenseChebConv, self).__init__()
        self._in_feats = in_feats
        self._out_feats = out_feats
        self._k = k
        self.W = nn.Parameter(th.Tensor(k, in_feats, out_feats))
        if bias:
            self.bias = nn.Parameter(th.Tensor(out_feats))
        else:
            self.register_buffer('bias', None)
        self.reset_parameters()

    def reset_parameters(self):
        """Reinitialize learnable parameters."""
        if self.bias is not None:
            init.zeros_(self.bias)
        for i in range(self._k):
            init.xavier_normal_(self.W[i], init.calculate_gain('relu'))

    def forward(self, adj, feat, lambda_max=None):
        r"""Compute (Dense) Chebyshev Spectral Graph Convolution layer.

        Parameters
        ----------
        adj : torch.Tensor
            The adjacency matrix of the graph to apply Graph Convolution on,
            should be of shape :math:`(N, N)`, where a row represents the destination
            and a column represents the source.
        feat : torch.Tensor
            The input feature of shape :math:`(N, D_{in})` where :math:`D_{in}`
            is size of input feature, :math:`N` is the number of nodes.
        lambda_max : float or None, optional
            A float value indicates the largest eigenvalue of given graph.
            Default: None.

        Returns
        -------
        torch.Tensor
            The output feature of shape :math:`(N, D_{out})` where :math:`D_{out}`
            is size of output feature.
        """
        A = adj.to(feat)
        num_nodes = A.shape[0]

        in_degree = 1 / A.sum(dim=1).clamp(min=1).sqrt()
        D_invsqrt = th.diag(in_degree)
        I = th.eye(num_nodes).to(A)
        L = I - D_invsqrt @ A @ D_invsqrt

        if lambda_max is None:
            lambda_ = th.eig(L)[0][:, 0]
            lambda_max = lambda_.max()

        L_hat = 2 * L / lambda_max - I
        Z = [th.eye(num_nodes).to(A)]
        for i in range(1, self._k):
            if i == 1:
                Z.append(L_hat)
            else:
                Z.append(2 * L_hat @ Z[-1] - Z[-2])

        Zs = th.stack(Z, 0)  # (k, n, n)

        Zh = (Zs @ feat.unsqueeze(0) @ self.W)
        Zh = Zh.sum(0)

        if self.bias is not None:
            Zh = Zh + self.bias
        return Zh
