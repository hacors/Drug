.. _tutorials1-index:

Graph neural networks and its variants
====================================

* **Graph convolutional network (GCN)** `[research paper] <https://arxiv.org/abs/1609.02907>`__ `[tutorial]
  <1_gnn/1_gcn.html>`__ `[Pytorch code]
  <https://github.com/dmlc/dgl/blob/master/examples/pytorch/gcn>`__
  `[MXNet code]
  <https://github.com/dmlc/dgl/tree/master/examples/mxnet/gcn>`__:
  This is the most basic GCN. The tutorial covers the basic uses of DGL APIs.

* **Graph attention network (GAT)** `[research paper] <https://arxiv.org/abs/1710.10903>`__ `[tutorial]
  <1_gnn/9_gat.html>`__ `[Pytorch code]
  <https://github.com/dmlc/dgl/blob/master/examples/pytorch/gat>`__
  `[MXNet code]
  <https://github.com/dmlc/dgl/tree/master/examples/mxnet/gat>`__:
  GAT extends the GCN functionality by deploying multi-head attention
  among neighborhood of a node. This greatly enhances the capacity and
  expressiveness of the model.

* **Relational-GCN** `[research paper] <https://arxiv.org/abs/1703.06103>`__ `[tutorial]
  <1_gnn/4_rgcn.html>`__ `[Pytorch code]
  <https://github.com/dmlc/dgl/tree/master/examples/pytorch/rgcn>`__
  `[MXNet code]
  <https://github.com/dmlc/dgl/tree/master/examples/mxnet/rgcn>`__:
  Relational-GCN allows multiple edges among two entities of a
  graph. Edges with distinct relationships are encoded differently. 

* **Line graph neural network (LGNN)** `[research paper] <https://arxiv.org/abs/1705.08415>`__ `[tutorial]
  <1_gnn/6_line_graph.html>`__ `[Pytorch code]
  <https://github.com/dmlc/dgl/tree/master/examples/pytorch/line_graph>`__:
  This network focuses on community detection by inspecting graph structures. It
  uses representations of both the original graph and its line-graph
  companion. In addition to demonstrating how an algorithm can harness multiple
  graphs, this implementation shows how you can judiciously mix simple tensor
  operations and sparse-matrix tensor operations, along with message-passing with
  DGL.

* **Stochastic steady-state embedding (SSE)** `[research paper] <http://proceedings.mlr.press/v80/dai18a/dai18a.pdf>`__ `[tutorial]
  <1_gnn/8_sse_mx.html>`__ `[MXNet code]
  <https://github.com/dmlc/dgl/blob/master/examples/mxnet/sse>`__:
  SSE is an example to illustrate the co-design of both algorithm and
  system. Sampling to guarantee asymptotic convergence while lowering
  complexity and batching across samples for maximum parallelism. The emphasis 
  here is that a giant graph that cannot fit comfortably on one GPU card. 
