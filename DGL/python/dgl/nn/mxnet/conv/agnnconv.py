"""MXNet Module for Attention-based Graph Neural Network layer"""
# pylint: disable= no-member, arguments-differ, invalid-name
import mxnet as mx
from mxnet.gluon import nn

from .... import function as fn
from ..softmax import edge_softmax
from ..utils import normalize

class AGNNConv(nn.Block):
    r"""Attention-based Graph Neural Network layer from paper `Attention-based
    Graph Neural Network for Semi-Supervised Learning
    <https://arxiv.org/abs/1803.03735>`__.

    .. math::
        H^{l+1} = P H^{l}

    where :math:`P` is computed as:

    .. math::
        P_{ij} = \mathrm{softmax}_i ( \beta \cdot \cos(h_i^l, h_j^l))

    Parameters
    ----------
    init_beta : float, optional
        The :math:`\beta` in the formula.
    learn_beta : bool, optional
        If True, :math:`\beta` will be learnable parameter.
    """
    def __init__(self,
                 init_beta=1.,
                 learn_beta=True):
        super(AGNNConv, self).__init__()
        with self.name_scope():
            self.beta = self.params.get('beta',
                                        shape=(1,),
                                        grad_req='write' if learn_beta else 'null',
                                        init=mx.init.Constant(init_beta))

    def forward(self, graph, feat):
        r"""Compute AGNN Layer.

        Parameters
        ----------
        graph : DGLGraph
            The graph.
        feat : mxnet.NDArray
            The input feature of shape :math:`(N, *)` :math:`N` is the
            number of nodes, and :math:`*` could be of any shape.

        Returns
        -------
        mxnet.NDArray
            The output feature of shape :math:`(N, *)` where :math:`*`
            should be the same as input shape.
        """
        graph = graph.local_var()
        graph.ndata['h'] = feat
        graph.ndata['norm_h'] = normalize(feat, p=2, axis=-1)
        # compute cosine distance
        graph.apply_edges(fn.u_dot_v('norm_h', 'norm_h', 'cos'))
        cos = graph.edata.pop('cos')
        e = self.beta.data(feat.context) * cos
        graph.edata['p'] = edge_softmax(graph, e)
        graph.update_all(fn.u_mul_e('h', 'p', 'm'), fn.sum('m', 'h'))
        return graph.ndata.pop('h')
