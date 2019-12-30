"""Classes for heterogeneous graphs."""
from collections import defaultdict
from contextlib import contextmanager
import networkx as nx
import numpy as np

from . import graph_index
from . import heterograph_index
from . import utils
from . import backend as F
from . import init
from .runtime import ir, scheduler, Runtime, GraphAdapter
from .frame import Frame, FrameRef, frame_like, sync_frame_initializer
from .view import HeteroNodeView, HeteroNodeDataView, HeteroEdgeView, HeteroEdgeDataView
from .base import ALL, SLICE_FULL, NTYPE, NID, ETYPE, EID, is_all, DGLError, dgl_warning
from .udf import NodeBatch, EdgeBatch

__all__ = ['DGLHeteroGraph', 'combine_names']

class DGLHeteroGraph(object):
    """Base heterogeneous graph class.

    **Do NOT instantiate from this class directly; use** :mod:`conversion methods
    <dgl.convert>` **instead.**

    A Heterogeneous graph is defined as a graph with node types and edge
    types.

    If two edges share the same edge type, then their source nodes, as well
    as their destination nodes, also have the same type (the source node
    types don't have to be the same as the destination node types).

    Examples
    --------
    Suppose that we want to construct the following heterogeneous graph:

    .. graphviz::

       digraph G {
           Alice -> Bob [label=follows]
           Bob -> Carol [label=follows]
           Alice -> Tetris [label=plays]
           Bob -> Tetris [label=plays]
           Bob -> Minecraft [label=plays]
           Carol -> Minecraft [label=plays]
           Nintendo -> Tetris [label=develops]
           Mojang -> Minecraft [label=develops]
           {rank=source; Alice; Bob; Carol}
           {rank=sink; Nintendo; Mojang}
       }

    And suppose that one maps the users, games and developers to the following
    IDs:

    =========  =====  ===  =====
    User name  Alice  Bob  Carol
    =========  =====  ===  =====
    User ID    0      1    2
    =========  =====  ===  =====

    =========  ======  =========
    Game name  Tetris  Minecraft
    =========  ======  =========
    Game ID    0       1
    =========  ======  =========

    ==============  ========  ======
    Developer name  Nintendo  Mojang
    ==============  ========  ======
    Developer ID    0         1
    ==============  ========  ======

    One can construct the graph as follows:

    >>> follows_g = dgl.graph([(0, 1), (1, 2)], 'user', 'follows')
    >>> plays_g = dgl.bipartite([(0, 0), (1, 0), (1, 1), (2, 1)], 'user', 'plays', 'game')
    >>> devs_g = dgl.bipartite([(0, 0), (1, 1)], 'developer', 'develops', 'game')
    >>> g = dgl.hetero_from_relations([follows_g, plays_g, devs_g])

    Or equivalently

    >>> g = dgl.heterograph({
    ...     ('user', 'follows', 'user'): [(0, 1), (1, 2)],
    ...     ('user', 'plays', 'game'): [(0, 0), (1, 0), (1, 1), (2, 1)],
    ...     ('developer', 'develops', 'game'): [(0, 0), (1, 1)],
    ...     })

    :func:`dgl.graph` and :func:`dgl.bipartite` can create a graph from a variety of
    data types including:

    * edge list
    * edge tuples
    * networkx graph
    * scipy sparse matrix

    Click the function names for more details.

    Then one can query the graph structure by specifying the ``ntype`` or ``etype`` arguments:

    >>> g.number_of_nodes('user')
    3
    >>> g.number_of_edges('plays')
    4
    >>> g.out_degrees(etype='develops')  # out-degrees of source nodes of 'develops' relation
    tensor([1, 1])
    >>> g.in_edges(0, etype='develops')  # in-edges of destination node 0 of 'develops' relation
    (tensor([0]), tensor([0]))

    Or on the sliced graph for an edge type:

    >>> g['plays'].number_of_edges()
    4
    >>> g['develops'].out_degrees()
    tensor([1, 1])
    >>> g['develops'].in_edges(0)
    (tensor([0]), tensor([0]))

    Node type names must be distinct (no two types have the same name). Edge types could
    have the same name but they must be distinguishable by the ``(src_type, edge_type, dst_type)``
    triplet (called *canonical edge type*).

    For example, suppose a graph that has two types of relation "user-watches-movie"
    and "user-watches-TV" as follows:

    >>> g0 = dgl.bipartite([(0, 1), (1, 0), (1, 1)], 'user', 'watches', 'movie')
    >>> g1 = dgl.bipartite([(0, 0), (1, 1)], 'user', 'watches', 'TV')
    >>> GG = dgl.hetero_from_relations([g0, g1]) # Merge the two graphs

    To distinguish between the two "watches" edge type, one must specify a full triplet:

    >>> GG.number_of_edges(('user', 'watches', 'movie'))
    3
    >>> GG.number_of_edges(('user', 'watches', 'TV'))
    2
    >>> GG['user', 'watches', 'movie'].out_degrees()
    tensor([1, 2])

    Using only one single edge type string "watches" is ambiguous and will cause error:

    >>> GG.number_of_edges('watches')  # AMBIGUOUS!!

    In many cases, there is only one type of nodes or one type of edges, and the ``ntype``
    and ``etype`` argument could be omitted. This is very common when using the sliced
    graph, which usually contains only one edge type, and sometimes only one node type:

    >>> g['follows'].number_of_nodes()  # OK!! because g['follows'] only has one node type 'user'
    3
    >>> g['plays'].number_of_nodes()  # ERROR!! There are two types 'user' and 'game'.
    >>> g['plays'].number_of_edges()  # OK!! because there is only one edge type 'plays'

    Metagraph
    ---------
    For each heterogeneous graph, one can often infer the *metagraph*, the template of
    edge connections showing how many types of nodes and edges exist in the graph, and
    how each edge type could connect between node types.

    One can analyze the example gameplay graph above and figure out the metagraph as
    follows:

    .. graphviz::

       digraph G {
           User -> User [label=follows]
           User -> Game [label=plays]
           Developer -> Game [label=develops]
       }


    Parameters
    ----------
    gidx : HeteroGraphIndex
        Graph index object.
    ntypes : list of str
        Node type list. ``ntypes[i]`` stores the name of node type i.
    etypes : list of str
        Edge type list. ``etypes[i]`` stores the name of edge type i.
    node_frames : list of FrameRef, optional
        Node feature storage. If None, empty frame is created.
        Otherwise, ``node_frames[i]`` stores the node features
        of node type i. (default: None)
    edge_frames : list of FrameRef, optional
        Edge feature storage. If None, empty frame is created.
        Otherwise, ``edge_frames[i]`` stores the edge features
        of edge type i. (default: None)
    multigraph : bool, optional
        Whether the graph would be a multigraph. If none, the flag will be
        determined by scanning the whole graph. (default: None)
    readonly : bool, optional
        Whether the graph structure is read-only. Currently, only readonly
        is allowed. (default: True).
    """
    # pylint: disable=unused-argument
    def __init__(self,
                 gidx,
                 ntypes,
                 etypes,
                 node_frames=None,
                 edge_frames=None,
                 multigraph=None,
                 readonly=True):
        assert readonly, "Only readonly heterogeneous graphs are supported"

        self._graph = gidx
        self._nx_metagraph = None
        self._ntypes = ntypes
        self._etypes = etypes
        self._canonical_etypes = make_canonical_etypes(etypes, ntypes, self._graph.metagraph)
        # An internal map from etype to canonical etype tuple.
        # If two etypes have the same name, an empty tuple is stored instead to indicte ambiguity.
        self._etype2canonical = {}
        for i, ety in enumerate(etypes):
            if ety in self._etype2canonical:
                self._etype2canonical[ety] = tuple()
            else:
                self._etype2canonical[ety] = self._canonical_etypes[i]
        self._ntypes_invmap = {t : i for i, t in enumerate(ntypes)}
        self._etypes_invmap = {t : i for i, t in enumerate(self._canonical_etypes)}

        # node and edge frame
        if node_frames is None:
            node_frames = [None] * len(self._ntypes)
        node_frames = [FrameRef(Frame(num_rows=self._graph.number_of_nodes(i)))
                       if frame is None else frame
                       for i, frame in enumerate(node_frames)]
        self._node_frames = node_frames

        if edge_frames is None:
            edge_frames = [None] * len(self._etypes)
        edge_frames = [FrameRef(Frame(num_rows=self._graph.number_of_edges(i)))
                       if frame is None else frame
                       for i, frame in enumerate(edge_frames)]
        self._edge_frames = edge_frames

        # message indicators
        self._msg_indices = [None] * len(self._etypes)
        self._msg_frames = []
        for i in range(len(self._etypes)):
            frame = FrameRef(Frame(num_rows=self._graph.number_of_edges(i)))
            frame.set_initializer(init.zero_initializer)
            self._msg_frames.append(frame)

        self._is_multigraph = multigraph

    def _get_msg_index(self, etid):
        if self._msg_indices[etid] is None:
            self._msg_indices[etid] = utils.zero_index(
                size=self._graph.number_of_edges(etid))
        return self._msg_indices[etid]

    def _set_msg_index(self, etid, index):
        self._msg_indices[etid] = index

    def __repr__(self):
        if len(self.ntypes) == 1 and len(self.etypes) == 1:
            ret = ('Graph(num_nodes={node}, num_edges={edge},\n'
                   '      ndata_schemes={ndata}\n'
                   '      edata_schemes={edata})')
            return ret.format(node=self.number_of_nodes(), edge=self.number_of_edges(),
                              ndata=str(self.node_attr_schemes()),
                              edata=str(self.edge_attr_schemes()))
        else:
            ret = ('Graph(num_nodes={node},\n'
                   '      num_edges={edge},\n'
                   '      metagraph={meta})')
            nnode_dict = {self.ntypes[i] : self._graph.number_of_nodes(i)
                          for i in range(len(self.ntypes))}
            nedge_dict = {self.canonical_etypes[i] : self._graph.number_of_edges(i)
                          for i in range(len(self.etypes))}
            meta = str(self.metagraph.edges())
            return ret.format(node=nnode_dict, edge=nedge_dict, meta=meta)

    #################################################################
    # Mutation operations
    #################################################################

    def add_nodes(self, num, data=None, ntype=None):
        """Add multiple new nodes of the same node type

        Currently not supported.
        """
        raise DGLError('Mutation is not supported in heterograph.')

    def add_edge(self, u, v, data=None, etype=None):
        """Add an edge of ``etype`` between u of the source node type, and v
        of the destination node type..

        Currently not supported.
        """
        raise DGLError('Mutation is not supported in heterograph.')

    def add_edges(self, u, v, data=None, etype=None):
        """Add multiple edges of ``etype`` between list of source nodes ``u``
        and list of destination nodes ``v`` of type ``vtype``.  A single edge
        is added between every pair of ``u[i]`` and ``v[i]``.

        Currently not supported.
        """
        raise DGLError('Mutation is not supported in heterograph.')

    #################################################################
    # Metagraph query
    #################################################################

    @property
    def ntypes(self):
        """Return the list of node types of this graph.

        Returns
        -------
        list of str

        Examples
        --------

        >>> follows_g = dgl.graph([(0, 1), (1, 2)], 'user', 'follows')
        >>> plays_g = dgl.bipartite([(0, 0), (1, 0), (1, 1), (2, 1)], 'user', 'plays', 'game')
        >>> g = dgl.hetero_from_relations([follows_g, plays_g])
        >>> g.ntypes
        ['user', 'game']
        """
        return self._ntypes

    @property
    def etypes(self):
        """Return the list of edge types of this graph.

        Returns
        -------
        list of str

        Examples
        --------

        >>> follows_g = dgl.graph([(0, 1), (1, 2)], 'user', 'follows')
        >>> plays_g = dgl.bipartite([(0, 0), (1, 0), (1, 1), (2, 1)], 'user', 'plays', 'game')
        >>> g = dgl.hetero_from_relations([follows_g, plays_g])
        >>> g.etypes
        ['follows', 'plays']
        """
        return self._etypes

    @property
    def canonical_etypes(self):
        """Return the list of canonical edge types of this graph.

        A canonical edge type is a tuple of string (src_type, edge_type, dst_type).

        Returns
        -------
        list of 3-tuples

        Examples
        --------

        >>> follows_g = dgl.graph([(0, 1), (1, 2)], 'user', 'follows')
        >>> plays_g = dgl.bipartite([(0, 0), (1, 0), (1, 1), (2, 1)], 'user', 'plays', 'game')
        >>> g = dgl.hetero_from_relations([follows_g, plays_g])
        >>> g.canonical_etypes
        [('user', 'follows', 'user'), ('user', 'plays', 'game')]
        """
        return self._canonical_etypes

    @property
    def metagraph(self):
        """Return the metagraph as networkx.MultiDiGraph.

        The nodes are labeled with node type names.
        The edges have their keys holding the edge type names.

        Returns
        -------
        networkx.MultiDiGraph

        Examples
        --------

        >>> follows_g = dgl.graph([(0, 1), (1, 2)], 'user', 'follows')
        >>> plays_g = dgl.bipartite([(0, 0), (1, 0), (1, 1), (2, 1)], 'user', 'plays', 'game')
        >>> g = dgl.hetero_from_relations([follows_g, plays_g])
        >>> meta_g = g.metagraph

        The metagraph then has two nodes and two edges.

        >>> meta_g.nodes()
        NodeView(('user', 'game'))
        >>> meta_g.number_of_nodes()
        2
        >>> meta_g.edges()
        OutMultiEdgeDataView([('user', 'user'), ('user', 'game')])
        >>> meta_g.number_of_edges()
        2
        """
        if self._nx_metagraph is None:
            nx_graph = self._graph.metagraph.to_networkx()
            self._nx_metagraph = nx.MultiDiGraph()
            for u_v in nx_graph.edges:
                srctype, etype, dsttype = self.canonical_etypes[nx_graph.edges[u_v]['id']]
                self._nx_metagraph.add_edge(srctype, dsttype, etype)
        return self._nx_metagraph

    def to_canonical_etype(self, etype):
        """Convert edge type to canonical etype: (srctype, etype, dsttype).

        The input can already be a canonical tuple.

        Parameters
        ----------
        etype : str or tuple of str
            Edge type

        Returns
        -------
        tuple of str

        Examples
        --------

        Instantiate a heterograph.

        >>> g1 = dgl.graph([(0, 1), (1, 2)], 'user', 'follows')
        >>> g2 = dgl.bipartite([(0, 0), (1, 0), (1, 1), (2, 1)], 'user', 'plays', 'game')
        >>> g3 = dgl.bipartite([(0, 0), (1, 1)], 'developer', 'follows', 'game')
        >>> g = dgl.hetero_from_relations([g1, g2, g3])

        Get canonical edge types.

        >>> g.to_canonical_etype('plays')
        ('user', 'plays', 'game')
        >>> g.to_canonical_etype(('user', 'plays', 'game'))
        ('user', 'plays', 'game')
        >>> g.to_canonical_etype('follows')
        DGLError: Edge type "follows" is ambiguous.
        Please use canonical etype type in the form of (srctype, etype, dsttype)
        """
        if isinstance(etype, tuple):
            return etype
        else:
            ret = self._etype2canonical.get(etype, None)
            if ret is None:
                raise DGLError('Edge type "{}" does not exist.'.format(etype))
            if len(ret) == 0:
                raise DGLError('Edge type "%s" is ambiguous. Please use canonical etype '
                               'type in the form of (srctype, etype, dsttype)' % etype)
            return ret

    def get_ntype_id(self, ntype):
        """Return the id of the given node type.

        ntype can also be None. If so, there should be only one node type in the
        graph.

        Parameters
        ----------
        ntype : str
            Node type

        Returns
        -------
        int
        """
        if ntype is None:
            if self._graph.number_of_ntypes() != 1:
                raise DGLError('Node type name must be specified if there are more than one '
                               'node types.')
            return 0
        ntid = self._ntypes_invmap.get(ntype, None)
        if ntid is None:
            raise DGLError('Node type "{}" does not exist.'.format(ntype))
        return ntid

    def get_etype_id(self, etype):
        """Return the id of the given edge type.

        etype can also be None. If so, there should be only one edge type in the
        graph.

        Parameters
        ----------
        etype : str or tuple of str
            Edge type

        Returns
        -------
        int
        """
        if etype is None:
            if self._graph.number_of_etypes() != 1:
                raise DGLError('Edge type name must be specified if there are more than one '
                               'edge types.')
            return 0
        etid = self._etypes_invmap.get(self.to_canonical_etype(etype), None)
        if etid is None:
            raise DGLError('Edge type "{}" does not exist.'.format(etype))
        return etid

    #################################################################
    # View
    #################################################################

    @property
    def nodes(self):
        """Return a node view that can be used to set/get feature
        data of a single node type.

        Examples
        --------
        The following example uses PyTorch backend.

        To set features of all users

        >>> g = dgl.graph([(0, 1), (1, 2)], 'user', 'follows')
        >>> g.nodes['user'].data['h'] = torch.zeros(3, 5)

        See Also
        --------
        ndata
        """
        return HeteroNodeView(self)

    @property
    def ndata(self):
        """Return the data view of all the nodes.

        **Only works if the graph has one node type.**

        Examples
        --------
        The following example uses PyTorch backend.

        To set features of all nodes in a heterogeneous graph
        with only one node type:

        >>> g = dgl.graph([(0, 1), (1, 2)], 'user', 'follows')
        >>> g.ndata['h'] = torch.zeros(3, 5)

        See Also
        --------
        nodes
        """
        return HeteroNodeDataView(self, None, ALL)

    @property
    def edges(self):
        """Return an edge view that can be used to set/get feature
        data of a single edge type.

        Examples
        --------
        The following example uses PyTorch backend.

        To set features of all "play" relationships:

        >>> g = dgl.bipartite([(0, 0), (1, 0), (1, 2)], 'user', 'plays', 'game')
        >>> g.edges['plays'].data['h'] = torch.zeros(3, 4)

        See Also
        --------
        edata
        """
        return HeteroEdgeView(self)

    @property
    def edata(self):
        """Return the data view of all the edges.

        **Only works if the graph has one edge type.**

        Examples
        --------
        The following example uses PyTorch backend.

        To set features of all edges in a heterogeneous graph
        with only one edge type:

        >>> g = dgl.graph([(0, 1), (1, 2)], 'user', 'follows')
        >>> g.edata['h'] = torch.zeros(2, 5)

        See Also
        --------
        edges
        """
        return HeteroEdgeDataView(self, None, ALL)

    def _find_etypes(self, key):
        etypes = [
            i for i, (srctype, etype, dsttype) in enumerate(self._canonical_etypes) if
            (key[0] == SLICE_FULL or key[0] == srctype) and
            (key[1] == SLICE_FULL or key[1] == etype) and
            (key[2] == SLICE_FULL or key[2] == dsttype)]
        return etypes

    def __getitem__(self, key):
        """Return the relation slice of this graph.

        A relation slice is accessed with ``self[srctype, etype, dsttype]``, where
        ``srctype``, ``etype``, and ``dsttype`` can be either a string or a full
        slice (``:``) representing wildcard (i.e. any source/edge/destination type).

        A relation slice is a homogeneous (with one node type and one edge type) or
        bipartite (with two node types and one edge type) graph, transformed from
        the original heterogeneous graph.

        If there is only one canonical edge type found, then the returned relation
        slice would be a subgraph induced from the original graph.  That is, it is
        equivalent to ``self.edge_type_subgraph(etype)``.  The node and edge features
        of the returned graph would be shared with thew original graph.

        If there are multiple canonical edge type found, then the source/edge/destination
        node types would be a *concatenation* of original node/edge types.  The
        new source/destination node type would have the concatenation determined by
        :func:`dgl.combine_names() <dgl.combine_names>` called on original source/destination
        types as its name.  The source/destination node would be formed by concatenating the
        common features of the original source/destination types, therefore they are not
        shared with the original graph.  Edge type is similar.
        """
        err_msg = "Invalid slice syntax. Use G['etype'] or G['srctype', 'etype', 'dsttype'] " +\
                  "to get view of one relation type. Use : to slice multiple types (e.g. " +\
                  "G['srctype', :, 'dsttype'])."

        if not isinstance(key, tuple):
            key = (SLICE_FULL, key, SLICE_FULL)

        if len(key) != 3:
            raise DGLError(err_msg)

        etypes = self._find_etypes(key)
        if len(etypes) == 1:
            # no ambiguity: return the unitgraph itself
            srctype, etype, dsttype = self._canonical_etypes[etypes[0]]
            stid = self.get_ntype_id(srctype)
            etid = self.get_etype_id((srctype, etype, dsttype))
            dtid = self.get_ntype_id(dsttype)
            new_g = self._graph.get_relation_graph(etid)

            if stid == dtid:
                new_ntypes = [srctype]
                new_nframes = [self._node_frames[stid]]
            else:
                new_ntypes = [srctype, dsttype]
                new_nframes = [self._node_frames[stid], self._node_frames[dtid]]
            new_etypes = [etype]
            new_eframes = [self._edge_frames[etid]]

            return DGLHeteroGraph(new_g, new_ntypes, new_etypes, new_nframes, new_eframes)
        else:
            flat = self._graph.flatten_relations(etypes)
            new_g = flat.graph

            # merge frames
            stids = flat.induced_srctype_set.asnumpy()
            dtids = flat.induced_dsttype_set.asnumpy()
            etids = flat.induced_etype_set.asnumpy()
            new_ntypes = [combine_names(self.ntypes, stids)]
            if new_g.number_of_ntypes() == 2:
                new_ntypes.append(combine_names(self.ntypes, dtids))
                new_nframes = [
                    combine_frames(self._node_frames, stids),
                    combine_frames(self._node_frames, dtids)]
            else:
                assert np.array_equal(stids, dtids)
                new_nframes = [combine_frames(self._node_frames, stids)]
            new_etypes = [combine_names(self.etypes, etids)]
            new_eframes = [combine_frames(self._edge_frames, etids)]

            # create new heterograph
            new_hg = DGLHeteroGraph(new_g, new_ntypes, new_etypes, new_nframes, new_eframes)

            src = new_ntypes[0]
            dst = new_ntypes[1] if new_g.number_of_ntypes() == 2 else src
            # put the parent node/edge type and IDs
            new_hg.nodes[src].data[NTYPE] = F.zerocopy_from_dgl_ndarray(flat.induced_srctype)
            new_hg.nodes[src].data[NID] = F.zerocopy_from_dgl_ndarray(flat.induced_srcid)
            new_hg.nodes[dst].data[NTYPE] = F.zerocopy_from_dgl_ndarray(flat.induced_dsttype)
            new_hg.nodes[dst].data[NID] = F.zerocopy_from_dgl_ndarray(flat.induced_dstid)
            new_hg.edata[ETYPE] = F.zerocopy_from_dgl_ndarray(flat.induced_etype)
            new_hg.edata[EID] = F.zerocopy_from_dgl_ndarray(flat.induced_eid)

            return new_hg

    #################################################################
    # Graph query
    #################################################################

    def number_of_nodes(self, ntype=None):
        """Return the number of nodes of the given type in the heterograph.

        Parameters
        ----------
        ntype : str, optional
            The node type. Can be omitted if there is only one node type
            in the graph. (Default: None)

        Returns
        -------
        int
            The number of nodes

        Examples
        --------

        >>> g = dgl.graph([(0, 1), (1, 2)], 'user', 'follows')
        >>> g.number_of_nodes('user')
        3
        >>> g.number_of_nodes()
        3
        """
        return self._graph.number_of_nodes(self.get_ntype_id(ntype))

    def number_of_edges(self, etype=None):
        """Return the number of edges of the given type in the heterograph.

        Parameters
        ----------
        etype : str or tuple of str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph.

        Returns
        -------
        int
            The number of edges

        Examples
        --------

        >>> g = dgl.graph([(0, 1), (1, 2)], 'user', 'follows')
        >>> g.number_of_edges(('user', 'follows', 'user'))
        >>> g.number_of_edges('follows')
        2
        >>> g.number_of_edges()
        2
        """
        return self._graph.number_of_edges(self.get_etype_id(etype))

    @property
    def is_multigraph(self):
        """Whether the graph is a multigraph

        Returns
        -------
        bool
            True if the graph is a multigraph, False otherwise.
        """
        if self._is_multigraph is None:
            return self._graph.is_multigraph()
        else:
            return self._is_multigraph

    @property
    def is_readonly(self):
        """Whether the graph is readonly

        Returns
        -------
        bool
            True if the graph is readonly, False otherwise.
        """
        return self._graph.is_readonly()

    def has_node(self, vid, ntype=None):
        """Whether the graph has a node with a particular id and type.

        Parameters
        ----------
        vid : int
            The node ID.
        ntype : str, optional
            The node type. Can be omitted if there is only one node type
            in the graph. (Default: None)

        Returns
        -------
        bool
            True if the node exists, False otherwise

        Examples
        --------
        >>> g.has_node(0, 'user')
        True
        >>> g.has_node(4, 'user')
        False

        See Also
        --------
        has_nodes
        """
        return self._graph.has_node(self.get_ntype_id(ntype), vid)

    def has_nodes(self, vids, ntype=None):
        """Whether the graph has nodes with ids and a particular type.

        Parameters
        ----------
        vid : list or tensor
            The array of node IDs.
        ntype : str, optional
            The node type. Can be omitted if there is only one node type
            in the graph.

        Returns
        -------
        a : tensor
            Binary tensor indicating the existence of nodes with the specified ids and type.
            ``a[i]=1`` if the graph contains node ``vids[i]`` of type ``ntype``, 0 otherwise.

        Examples
        --------
        The following example uses PyTorch backend.

        >>> g.has_nodes([0, 1, 2, 3, 4], 'user')
        tensor([1, 1, 1, 0, 0])

        See Also
        --------
        has_node
        """
        vids = utils.toindex(vids)
        rst = self._graph.has_nodes(self.get_ntype_id(ntype), vids)
        return rst.tousertensor()

    def has_edge_between(self, u, v, etype=None):
        """Whether the graph has an edge (u, v) of type ``etype``.

        Parameters
        ----------
        u : int
            The node ID of source type.
        v : int
            The node ID of destination type.
        etype : str or tuple of str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph.

        Returns
        -------
        bool
            True if the edge is in the graph, False otherwise.

        Examples
        --------

        >>> g.has_edge_between(0, 1, ('user', 'plays', 'game'))
        True
        >>> g.has_edge_between(0, 2, ('user', 'plays', 'game'))
        False

        See Also
        --------
        has_edges_between
        """
        return self._graph.has_edge_between(self.get_etype_id(etype), u, v)

    def has_edges_between(self, u, v, etype=None):
        """Whether the graph has edges of type ``etype``.

        Parameters
        ----------
        u : list, tensor
            The node ID array of source type.
        v : list, tensor
            The node ID array of destination type.
        etype : str or tuple of str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph.

        Returns
        -------
        a : tensor
            Binary tensor indicating the existence of edges. ``a[i]=1`` if the graph
            contains edge ``(u[i], v[i])`` of type ``etype``, 0 otherwise.

        Examples
        --------
        The following example uses PyTorch backend.

        >>> g.has_edges_between([0, 0], [1, 2], ('user', 'plays', 'game'))
        tensor([1, 0])

        See Also
        --------
        has_edge_between
        """
        u = utils.toindex(u)
        v = utils.toindex(v)
        rst = self._graph.has_edges_between(self.get_etype_id(etype), u, v)
        return rst.tousertensor()

    def predecessors(self, v, etype=None):
        """Return the predecessors of node `v` in the graph with the specified
        edge type.

        Node `u` is a predecessor of `v` if an edge `(u, v)` with type `etype`
        exists in the graph.

        Parameters
        ----------
        v : int
            The destination node.
        etype : str or tuple of str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph. (Default: None)

        Returns
        -------
        tensor
            Array of predecessor node IDs with the specified edge type.

        Examples
        --------
        The following example uses PyTorch backend.

        >>> plays_g = dgl.bipartite([(0, 0), (1, 0), (1, 1), (2, 1)], 'user', 'plays', 'game')
        >>> devs_g = dgl.bipartite([(0, 0), (1, 1)], 'developer', 'develops', 'game')
        >>> g = dgl.hetero_from_relations([plays_g, devs_g])
        >>> g.predecessors(0, 'plays')
        tensor([0, 1])
        >>> g.predecessors(0, 'develops')
        tensor([0])

        See Also
        --------
        successors
        """
        return self._graph.predecessors(self.get_etype_id(etype), v).tousertensor()

    def successors(self, v, etype=None):
        """Return the successors of node `v` in the graph with the specified edge
        type.

        Node `u` is a successor of `v` if an edge `(v, u)` with type `etype` exists
        in the graph.

        Parameters
        ----------
        v : int
            The source node.
        etype : str or tuple of str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph. (Default: None)

        Returns
        -------
        tensor
            Array of successor node IDs with the specified edge type.

        Examples
        --------
        The following example uses PyTorch backend.

        >>> plays_g = dgl.bipartite([(0, 0), (1, 0), (1, 1), (2, 1)], 'user', 'plays', 'game')
        >>> follows_g = dgl.graph([(0, 1), (1, 2)], 'user', 'follows')
        >>> g = dgl.hetero_from_relations([plays_g, follows_g])
        >>> g.successors(0, 'plays')
        tensor([0])
        >>> g.successors(0, 'follows')
        tensor([1])

        See Also
        --------
        predecessors
        """
        return self._graph.successors(self.get_etype_id(etype), v).tousertensor()

    def edge_id(self, u, v, force_multi=False, etype=None):
        """Return the edge ID, or an array of edge IDs, between source node
        `u` and destination node `v`, with the specified edge type

        Parameters
        ----------
        u : int
            The node ID of source type.
        v : int
            The node ID of destination type.
        force_multi : bool, optional
            If False, will return a single edge ID if the graph is a simple graph.
            If True, will always return an array. (Default: False)
        etype : str or tuple of str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph.

        Returns
        -------
        int or tensor
            The edge ID if ``force_multi == True`` and the graph is a simple graph.
            The edge ID array otherwise.

        Examples
        --------
        The following example uses PyTorch backend.

        Instantiate a heterograph.

        >>> plays_g = dgl.bipartite([(0, 0), (1, 0), (1, 2), (2, 1)], 'user', 'plays', 'game')
        >>> follows_g = dgl.graph([(0, 1), (1, 2), (1, 2)], 'user', 'follows')
        >>> g = dgl.hetero_from_relations([plays_g, follows_g])

        Query for edge id.

        >>> plays_g.edge_id(1, 2, etype=('user', 'plays', 'game'))
        2
        >>> g.edge_id(1, 2, force_multi=True, etype=('user', 'follows', 'user'))
        tensor([1, 2])

        See Also
        --------
        edge_ids
        """
        idx = self._graph.edge_id(self.get_etype_id(etype), u, v)
        return idx.tousertensor() if force_multi or self._graph.is_multigraph() else idx[0]

    def edge_ids(self, u, v, force_multi=False, etype=None):
        """Return all edge IDs between source node array `u` and destination
        node array `v` with the specified edge type.

        Parameters
        ----------
        u : list, tensor
            The node ID array of source type.
        v : list, tensor
            The node ID array of destination type.
        force_multi : bool, optional
            Whether to always treat the graph as a multigraph. See the
            "Returns" for their effects. (Default: False)
        etype : str or tuple of str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph.

        Returns
        -------
        tensor, or (tensor, tensor, tensor)

            * If the graph is a simple graph and ``force_multi=False``, return
            a single edge ID array ``e``.  ``e[i]`` is the edge ID between ``u[i]``
            and ``v[i]``.

            * Otherwise, return three arrays ``(eu, ev, e)``.  ``e[i]`` is the ID
            of an edge between ``eu[i]`` and ``ev[i]``.  All edges between ``u[i]``
            and ``v[i]`` are returned.

        Notes
        -----
        If the graph is a simple graph, ``force_multi=False``, and no edge
        exists between some pairs of ``u[i]`` and ``v[i]``, the result is undefined
        and an empty tensor is returned.

        Examples
        --------
        The following example uses PyTorch backend.

        Instantiate a heterograph.

        >>> plays_g = dgl.bipartite([(0, 0), (1, 0), (1, 2), (2, 1)], 'user', 'plays', 'game')
        >>> follows_g = dgl.graph([(0, 1), (1, 2), (1, 2)], 'user', 'follows')
        >>> g = dgl.hetero_from_relations([plays_g, follows_g])

        Query for edge ids.

        >>> plays_g.edge_ids([0], [2], etype=('user', 'plays', 'game'))
        tensor([], dtype=torch.int64)
        >>> plays_g.edge_ids([1], [2], etype=('user', 'plays', 'game'))
        tensor([2])
        >>> g.edge_ids([1], [2], force_multi=True, etype=('user', 'follows', 'user'))
        (tensor([1, 1]), tensor([2, 2]), tensor([1, 2]))

        See Also
        --------
        edge_id
        """
        u = utils.toindex(u)
        v = utils.toindex(v)
        src, dst, eid = self._graph.edge_ids(self.get_etype_id(etype), u, v)
        if force_multi or self._graph.is_multigraph():
            return src.tousertensor(), dst.tousertensor(), eid.tousertensor()
        else:
            return eid.tousertensor()

    def find_edges(self, eid, etype=None):
        """Given an edge ID array with the specified type, return the source
        and destination node ID array ``s`` and ``d``.  ``s[i]`` and ``d[i]``
        are source and destination node ID for edge ``eid[i]``.

        Parameters
        ----------
        eid : list, tensor
            The edge ID array.
        etype : str or tuple of str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph. (Default: None)

        Returns
        -------
        tensor
            The source node ID array.
        tensor
            The destination node ID array.

        Examples
        --------
        The following example uses PyTorch backend.

        >>> g = dgl.bipartite([(0, 0), (1, 0), (1, 2)], 'user', 'plays', 'game')
        >>> g.find_edges([0, 2], ('user', 'plays', 'game'))
        (tensor([0, 1]), tensor([0, 2]))
        >>> g.find_edges([0, 2])
        (tensor([0, 1]), tensor([0, 2]))
        """
        eid = utils.toindex(eid)
        src, dst, _ = self._graph.find_edges(self.get_etype_id(etype), eid)
        return src.tousertensor(), dst.tousertensor()

    def in_edges(self, v, form='uv', etype=None):
        """Return the inbound edges of the node(s) with the specified type.

        Parameters
        ----------
        v : int, list, tensor
            The node id(s) of destination type.
        form : str, optional
            The return form. Currently support:

            - ``'eid'`` : one eid tensor
            - ``'all'`` : a tuple ``(u, v, eid)``
            - ``'uv'``  : a pair ``(u, v)``, default
        etype : str or tuple of str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph. (Default: None)

        Returns
        -------
        tensor or (tensor, tensor, tensor) or (tensor, tensor)
            All inbound edges to ``v`` are returned.

            * If ``form='eid'``, return a tensor for the ids of the
              inbound edges of the nodes with the specified type.
            * If ``form='all'``, return a 3-tuple of tensors
              ``(eu, ev, eid)``. ``eid[i]`` gives the ID of the
              edge from ``eu[i]`` to ``ev[i]``.
            * If ``form='uv'``, return a 2-tuple of tensors ``(eu, ev)``.
              ``eu[i]`` is the source node of an edge to ``ev[i]``.

        Examples
        --------
        The following example uses PyTorch backend.

        >>> g = dgl.bipartite([(0, 0), (1, 1), (1, 2)], 'user', 'plays', 'game')
        >>> g.in_edges([0, 2], form='eid')
        tensor([0, 2])
        >>> g.in_edges([0, 2], form='all')
        (tensor([0, 1]), tensor([0, 2]), tensor([0, 2]))
        >>> g.in_edges([0, 2], form='uv')
        (tensor([0, 1]), tensor([0, 2]))
        """
        v = utils.toindex(v)
        src, dst, eid = self._graph.in_edges(self.get_etype_id(etype), v)
        if form == 'all':
            return (src.tousertensor(), dst.tousertensor(), eid.tousertensor())
        elif form == 'uv':
            return (src.tousertensor(), dst.tousertensor())
        elif form == 'eid':
            return eid.tousertensor()
        else:
            raise DGLError('Invalid form:', form)

    def out_edges(self, u, form='uv', etype=None):
        """Return the outbound edges of the node(s) with the specified type.

        Parameters
        ----------
        u : int, list, tensor
            The node id(s) of source type.
        form : str, optional
            The return form. Currently support:

            - ``'eid'`` : one eid tensor
            - ``'all'`` : a tuple ``(u, v, eid)``
            - ``'uv'``  : a pair ``(u, v)``, default
        etype : str or tuple of str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph. (Default: None)

        Returns
        -------
        tensor or (tensor, tensor, tensor) or (tensor, tensor)
            All outbound edges from ``u`` are returned.

            * If ``form='eid'``, return a tensor for the ids of the outbound edges
              of the nodes with the specified type.
            * If ``form='all'``, return a 3-tuple of tensors ``(eu, ev, eid)``.
              ``eid[i]`` gives the ID of the edge from ``eu[i]`` to ``ev[i]``.
            * If ``form='uv'``, return a 2-tuple of tensors ``(eu, ev)``.
              ``ev[i]`` is the destination node of the edge from ``eu[i]``.

        Examples
        --------

        >>> g = dgl.bipartite([(0, 0), (1, 1), (1, 2)], 'user', 'plays', 'game')
        >>> g.out_edges([0, 1], form='eid')
        tensor([0, 1, 2])
        >>> g.out_edges([0, 1], form='all')
        (tensor([0, 1, 1]), tensor([0, 1, 2]), tensor([0, 1, 2]))
        >>> g.out_edges([0, 1], form='uv')
        (tensor([0, 1, 1]), tensor([0, 1, 2]))
        """
        u = utils.toindex(u)
        src, dst, eid = self._graph.out_edges(self.get_etype_id(etype), u)
        if form == 'all':
            return (src.tousertensor(), dst.tousertensor(), eid.tousertensor())
        elif form == 'uv':
            return (src.tousertensor(), dst.tousertensor())
        elif form == 'eid':
            return eid.tousertensor()
        else:
            raise DGLError('Invalid form:', form)

    def all_edges(self, form='uv', order=None, etype=None):
        """Return all edges with the specified type.

        Parameters
        ----------
        form : str, optional
            The return form. Currently support:

            - ``'eid'`` : one eid tensor
            - ``'all'`` : a tuple ``(u, v, eid)``
            - ``'uv'``  : a pair ``(u, v)``, default
        order : str or None
            The order of the returned edges. Currently support:

            - ``'srcdst'`` : sorted by their src and dst ids.
            - ``'eid'``    : sorted by edge Ids.
            - ``None``     : arbitrary order, default
        etype : str or tuple of str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph. (Default: None)

        Returns
        -------
        tensor or (tensor, tensor, tensor) or (tensor, tensor)

            * If ``form='eid'``, return a tensor for the ids of all edges
              with the specified type.
            * If ``form='all'``, return a 3-tuple of tensors ``(eu, ev, eid)``.
              ``eid[i]`` gives the ID of the edge from ``eu[i]`` to ``ev[i]``.
            * If ``form='uv'``, return a 2-tuple of tensors ``(eu, ev)``.
              ``ev[i]`` is the destination node of the edge from ``eu[i]``.

        Examples
        --------
        The following example uses PyTorch backend.

        >>> g = dgl.bipartite([(1, 1), (0, 0), (1, 2)], 'user', 'plays', 'game')
        >>> g.all_edges(form='eid', order='srcdst')
        tensor([1, 0, 2])
        >>> g.all_edges(form='all', order='srcdst')
        (tensor([0, 1, 1]), tensor([0, 1, 2]), tensor([1, 0, 2]))
        >>> g.all_edges(form='uv', order='eid')
        (tensor([1, 0, 1]), tensor([1, 0, 2]))
        """
        src, dst, eid = self._graph.edges(self.get_etype_id(etype), order)
        if form == 'all':
            return (src.tousertensor(), dst.tousertensor(), eid.tousertensor())
        elif form == 'uv':
            return (src.tousertensor(), dst.tousertensor())
        elif form == 'eid':
            return eid.tousertensor()
        else:
            raise DGLError('Invalid form:', form)

    def in_degree(self, v, etype=None):
        """Return the in-degree of node ``v`` with edges of type ``etype``.

        Parameters
        ----------
        v : int
            The node ID of destination type.
        etype : str or tuple of str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph. (Default: None)

        Returns
        -------
        int
            The in-degree.

        Examples
        --------

        Instantiate a heterograph.

        >>> plays_g = dgl.bipartite([(0, 0), (1, 0), (1, 2), (2, 1)], 'user', 'plays', 'game')
        >>> follows_g = dgl.graph([(0, 1), (1, 2), (1, 2)], 'user', 'follows')
        >>> g = dgl.hetero_from_relations([plays_g, follows_g])

        Query for node degree.

        >>> g.in_degree(0, 'plays')
        2
        >>> g.in_degree(0, 'follows')
        0

        See Also
        --------
        in_degrees
        """
        return self._graph.in_degree(self.get_etype_id(etype), v)

    def in_degrees(self, v=ALL, etype=None):
        """Return the in-degrees of nodes v with edges of type ``etype``.

        Parameters
        ----------
        v : list, tensor, optional.
            The node ID array of the destination type. Default is to return the
            degrees of all nodes.
        etype : str or tuple of str or None, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph. (Default: None)

        Returns
        -------
        d : tensor
            The in-degree array. ``d[i]`` gives the in-degree of node ``v[i]``
            with edges of type ``etype``.

        Examples
        --------
        The following example uses PyTorch backend.

        Instantiate a heterograph.

        >>> plays_g = dgl.bipartite([(0, 0), (1, 0), (1, 2), (2, 1)], 'user', 'plays', 'game')
        >>> follows_g = dgl.graph([(0, 1), (1, 2), (1, 2)], 'user', 'follows')
        >>> g = dgl.hetero_from_relations([plays_g, follows_g])

        Query for node degree.

        >>> g.in_degrees(0, 'plays')
        tensor([2])
        >>> g.in_degrees(etype='follows')
        tensor([0, 1, 2])

        See Also
        --------
        in_degree
        """
        etid = self.get_etype_id(etype)
        _, dtid = self._graph.metagraph.find_edge(etid)
        if is_all(v):
            v = utils.toindex(slice(0, self._graph.number_of_nodes(dtid)))
        else:
            v = utils.toindex(v)
        return self._graph.in_degrees(etid, v).tousertensor()

    def out_degree(self, u, etype=None):
        """Return the out-degree of node `u` with edges of type ``etype``.

        Parameters
        ----------
        u : int
            The node ID of source type.
        etype : str or tuple of str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph. (Default: None)

        Returns
        -------
        int
            The out-degree of node `u` with edges of type ``etype``.

        Examples
        --------

        Instantiate a heterograph.

        >>> plays_g = dgl.bipartite([(0, 0), (1, 0), (1, 2), (2, 1)], 'user', 'plays', 'game')
        >>> follows_g = dgl.graph([(0, 1), (1, 2), (1, 2)], 'user', 'follows')
        >>> g = dgl.hetero_from_relations([plays_g, follows_g])

        Query for node degree.

        >>> g.out_degree(0, 'plays')
        1
        >>> g.out_degree(1, 'follows')
        2

        See Also
        --------
        out_degrees
        """
        return self._graph.out_degree(self.get_etype_id(etype), u)

    def out_degrees(self, u=ALL, etype=None):
        """Return the out-degrees of nodes u with edges of type ``etype``.

        Parameters
        ----------
        u : list, tensor
            The node ID array of source type. Default is to return the degrees
            of all the nodes.
        etype : str or tuple of str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph. (Default: None)

        Returns
        -------
        d : tensor
            The out-degree array. ``d[i]`` gives the out-degree of node ``u[i]``
            with edges of type ``etype``.

        Examples
        --------
        The following example uses PyTorch backend.

        Instantiate a heterograph.

        >>> plays_g = dgl.bipartite([(0, 0), (1, 0), (1, 2), (2, 1)], 'user', 'plays', 'game')
        >>> follows_g = dgl.graph([(0, 1), (1, 2), (1, 2)], 'user', 'follows')
        >>> g = dgl.hetero_from_relations([plays_g, follows_g])

        Query for node degree.

        >>> g.out_degrees(0, 'plays')
        tensor([1])
        >>> g.out_degrees(etype='follows')
        tensor([1, 2, 0])

        See Also
        --------
        out_degree
        """
        etid = self.get_etype_id(etype)
        stid, _ = self._graph.metagraph.find_edge(etid)
        if is_all(u):
            u = utils.toindex(slice(0, self._graph.number_of_nodes(stid)))
        else:
            u = utils.toindex(u)
        return self._graph.out_degrees(etid, u).tousertensor()

    def _create_hetero_subgraph(self, sgi, induced_nodes, induced_edges):
        """Internal function to create a subgraph."""
        node_frames = [
            FrameRef(Frame(
                self._node_frames[i][induced_nodes_of_ntype],
                num_rows=len(induced_nodes_of_ntype)))
            for i, induced_nodes_of_ntype in enumerate(induced_nodes)]
        edge_frames = [
            FrameRef(Frame(
                self._edge_frames[i][induced_edges_of_etype],
                num_rows=len(induced_edges_of_etype)))
            for i, induced_edges_of_etype in enumerate(induced_edges)]

        hsg = DGLHeteroGraph(sgi.graph, self._ntypes, self._etypes, node_frames, edge_frames)
        hsg.is_subgraph = True
        for ntype, induced_nid in zip(self.ntypes, induced_nodes):
            hsg.nodes[ntype].data[NID] = induced_nid.tousertensor()
        for etype, induced_eid in zip(self.canonical_etypes, induced_edges):
            hsg.edges[etype].data[EID] = induced_eid.tousertensor()

        return hsg

    def subgraph(self, nodes):
        """Return the subgraph induced on given nodes.

        The metagraph of the returned subgraph is the same as the parent graph.
        Features are copied from the original graph.

        Parameters
        ----------
        nodes : dict[str->list or iterable]
            A dictionary mapping node types to node ID array for constructing
            subgraph. All nodes must exist in the graph.

        Returns
        -------
        G : DGLHeteroGraph
            The subgraph.

            The nodes and edges in the subgraph are relabeled using consecutive
            integers from 0.

            One can retrieve the mapping from subgraph node/edge ID to parent
            node/edge ID via ``dgl.NID`` and ``dgl.EID`` node/edge features of the
            subgraph.

        Examples
        --------
        The following example uses PyTorch backend.

        Instantiate a heterograph.

        >>> plays_g = dgl.bipartite([(0, 0), (1, 0), (1, 2), (2, 1)], 'user', 'plays', 'game')
        >>> follows_g = dgl.graph([(0, 1), (1, 2), (1, 2)], 'user', 'follows')
        >>> g = dgl.hetero_from_relations([plays_g, follows_g])
        >>> # Set node features
        >>> g.nodes['user'].data['h'] = torch.tensor([[0.], [1.], [2.]])

        Get subgraphs.

        >>> g.subgraph({'user': [4, 5]})
        An error occurs as these nodes do not exist.
        >>> sub_g = g.subgraph({'user': [1, 2]})
        >>> print(sub_g)
        Graph(num_nodes={'user': 2, 'game': 0},
              num_edges={('user', 'plays', 'game'): 0, ('user', 'follows', 'user'): 2},
              metagraph=[('user', 'game'), ('user', 'user')])

        Get the original node/edge indices.

        >>> sub_g['follows'].ndata[dgl.NID] # Get the node indices in the raw graph
        tensor([1, 2])
        >>> sub_g['follows'].edata[dgl.EID] # Get the edge indices in the raw graph
        tensor([1, 2])

        Get the copied node features.

        >>> sub_g.nodes['user'].data['h']
        tensor([[1.],
                [2.]])
        >>> sub_g.nodes['user'].data['h'] += 1
        >>> g.nodes['user'].data['h']          # Features are not shared.
        tensor([[0.],
                [1.],
                [2.]])

        See Also
        --------
        edge_subgraph
        """
        induced_nodes = [utils.toindex(nodes.get(ntype, [])) for ntype in self.ntypes]
        sgi = self._graph.node_subgraph(induced_nodes)
        induced_edges = sgi.induced_edges

        return self._create_hetero_subgraph(sgi, induced_nodes, induced_edges)

    def edge_subgraph(self, edges, preserve_nodes=False):
        """Return the subgraph induced on given edges.

        The metagraph of the returned subgraph is the same as the parent graph.

        Features are copied from the original graph.

        Parameters
        ----------
        edges : dict[str->list or iterable]
            A dictionary mapping edge types to edge ID array for constructing
            subgraph. All edges must exist in the subgraph.

            The edge types are characterized by triplets of
            ``(src type, etype, dst type)``.
        preserve_nodes : bool
            Whether to preserve all nodes or not. If false, all nodes
            without edges will be removed. (Default: False)

        Returns
        -------
        G : DGLHeteroGraph
            The subgraph.

            The nodes and edges are relabeled using consecutive integers from 0.

            One can retrieve the mapping from subgraph node/edge ID to parent
            node/edge ID via ``dgl.NID`` and ``dgl.EID`` node/edge features of the
            subgraph.

        Examples
        --------
        The following example uses PyTorch backend.

        Instantiate a heterograph.

        >>> plays_g = dgl.bipartite([(0, 0), (1, 0), (1, 2), (2, 1)], 'user', 'plays', 'game')
        >>> follows_g = dgl.graph([(0, 1), (1, 2), (1, 2)], 'user', 'follows')
        >>> g = dgl.hetero_from_relations([plays_g, follows_g])
        >>> # Set edge features
        >>> g.edges['follows'].data['h'] = torch.tensor([[0.], [1.], [2.]])

        Get subgraphs.

        >>> g.edge_subgraph({('user', 'follows', 'user'): [5, 6]})
        An error occurs as these edges do not exist.
        >>> sub_g = g.edge_subgraph({('user', 'follows', 'user'): [1, 2],
        >>>                          ('user', 'plays', 'game'): [2]})
        >>> print(sub_g)
        Graph(num_nodes={'user': 2, 'game': 1},
              num_edges={('user', 'plays', 'game'): 1, ('user', 'follows', 'user'): 2},
              metagraph=[('user', 'game'), ('user', 'user')])

        Get the original node/edge indices.

        >>> sub_g['follows'].ndata[dgl.NID] # Get the node indices in the raw graph
        tensor([1, 2])
        >>> sub_g['plays'].edata[dgl.EID]   # Get the edge indices in the raw graph
        tensor([2])

        Get the copied node features.

        >>> sub_g.edges['follows'].data['h']
        tensor([[1.],
                [2.]])
        >>> sub_g.edges['follows'].data['h'] += 1
        >>> g.edges['follows'].data['h']          # Features are not shared.
        tensor([[0.],
                [1.],
                [2.]])

        See Also
        --------
        subgraph
        """
        edges = {self.to_canonical_etype(etype): e for etype, e in edges.items()}
        induced_edges = [
            utils.toindex(edges.get(canonical_etype, []))
            for canonical_etype in self.canonical_etypes]
        sgi = self._graph.edge_subgraph(induced_edges, preserve_nodes)
        induced_nodes = sgi.induced_nodes

        return self._create_hetero_subgraph(sgi, induced_nodes, induced_edges)

    def node_type_subgraph(self, ntypes):
        """Return the subgraph induced on given node types.

        The metagraph of the returned subgraph is the subgraph of the original
        metagraph induced from the node types.

        Features are shared with the original graph.

        Parameters
        ----------
        ntypes : list[str]
            The node types

        Returns
        -------
        G : DGLHeteroGraph
            The subgraph.

        Examples
        --------
        The following example uses PyTorch backend.

        Instantiate a heterograph.

        >>> plays_g = dgl.bipartite([(0, 0), (1, 0), (1, 2), (2, 1)], 'user', 'plays', 'game')
        >>> follows_g = dgl.graph([(0, 1), (1, 2), (1, 2)], 'user', 'follows')
        >>> g = dgl.hetero_from_relations([plays_g, follows_g])
        >>> # Set node features
        >>> g.nodes['user'].data['h'] = torch.tensor([[0.], [1.], [2.]])

        Get subgraphs.

        >>> sub_g = g.node_type_subgraph(['user'])
        >>> print(sub_g)
        Graph(num_nodes=3, num_edges=3,
              ndata_schemes={'h': Scheme(shape=(1,), dtype=torch.float32)}
              edata_schemes={})

        Get the shared node features.

        >>> sub_g.nodes['user'].data['h']
        tensor([[0.],
                [1.],
                [2.]])
        >>> sub_g.nodes['user'].data['h'] += 1
        >>> g.nodes['user'].data['h']          # Features are shared.
        tensor([[1.],
                [2.],
                [3.]])

        See Also
        --------
        edge_type_subgraph
        """
        rel_graphs = []
        meta_edges = []
        induced_etypes = []
        node_frames = [self._node_frames[self.get_ntype_id(ntype)] for ntype in ntypes]
        edge_frames = []

        ntypes_invmap = {ntype: i for i, ntype in enumerate(ntypes)}
        srctype_id, dsttype_id, _ = self._graph.metagraph.edges('eid')
        for i in range(len(self._etypes)):
            srctype = self._ntypes[srctype_id[i]]
            dsttype = self._ntypes[dsttype_id[i]]

            if srctype in ntypes and dsttype in ntypes:
                meta_edges.append((ntypes_invmap[srctype], ntypes_invmap[dsttype]))
                rel_graphs.append(self._graph.get_relation_graph(i))
                induced_etypes.append(self.etypes[i])
                edge_frames.append(self._edge_frames[i])

        metagraph = graph_index.from_edge_list(meta_edges, True, True)
        hgidx = heterograph_index.create_heterograph_from_relations(metagraph, rel_graphs)
        hg = DGLHeteroGraph(hgidx, ntypes, induced_etypes, node_frames, edge_frames)
        return hg

    def edge_type_subgraph(self, etypes):
        """Return the subgraph induced on given edge types.

        The metagraph of the returned subgraph is the subgraph of the original metagraph
        induced from the edge types.

        Features are shared with the original graph.

        Parameters
        ----------
        etypes : list[str or tuple]
            The edge types

        Returns
        -------
        G : DGLHeteroGraph
            The subgraph.

        Examples
        --------
        The following example uses PyTorch backend.

        Instantiate a heterograph.

        >>> plays_g = dgl.bipartite([(0, 0), (1, 0), (1, 2), (2, 1)], 'user', 'plays', 'game')
        >>> follows_g = dgl.graph([(0, 1), (1, 2), (1, 2)], 'user', 'follows')
        >>> g = dgl.hetero_from_relations([plays_g, follows_g])
        >>> # Set edge features
        >>> g.edges['follows'].data['h'] = torch.tensor([[0.], [1.], [2.]])

        Get subgraphs.

        >>> sub_g = g.edge_type_subgraph(['follows'])
        >>> print(sub_g)
        Graph(num_nodes=3, num_edges=3,
              ndata_schemes={}
              edata_schemes={'h': Scheme(shape=(1,), dtype=torch.float32)})

        Get the shared edge features.

        >>> sub_g.edges['follows'].data['h']
        tensor([[0.],
                [1.],
                [2.]])
        >>> sub_g.edges['follows'].data['h'] += 1
        >>> g.edges['follows'].data['h']          # Features are shared.
        tensor([[1.],
                [2.],
                [3.]])

        See Also
        --------
        node_type_subgraph
        """
        etype_ids = [self.get_etype_id(etype) for etype in etypes]
        meta_src, meta_dst, _ = self._graph.metagraph.find_edges(utils.toindex(etype_ids))
        rel_graphs = [self._graph.get_relation_graph(i) for i in etype_ids]
        meta_src = meta_src.tonumpy()
        meta_dst = meta_dst.tonumpy()
        ntypes_invmap = {n: i for i, n in enumerate(set(meta_src) | set(meta_dst))}
        mapped_meta_src = [ntypes_invmap[v] for v in meta_src]
        mapped_meta_dst = [ntypes_invmap[v] for v in meta_dst]
        node_frames = [self._node_frames[i] for i in ntypes_invmap]
        edge_frames = [self._edge_frames[i] for i in etype_ids]
        induced_ntypes = [self._ntypes[i] for i in ntypes_invmap]
        induced_etypes = [self._etypes[i] for i in etype_ids]   # get the "name" of edge type

        metagraph = graph_index.from_edge_list((mapped_meta_src, mapped_meta_dst), True, True)
        hgidx = heterograph_index.create_heterograph_from_relations(metagraph, rel_graphs)
        hg = DGLHeteroGraph(hgidx, induced_ntypes, induced_etypes, node_frames, edge_frames)
        return hg

    def adjacency_matrix(self, transpose=None, ctx=F.cpu(), scipy_fmt=None, etype=None):
        """Return the adjacency matrix of edges of the given edge type.

        By default, a row of returned adjacency matrix represents the
        destination of an edge and the column represents the source.

        When transpose is True, a row represents the source and a column
        represents a destination.

        Parameters
        ----------
        transpose : bool, optional
            A flag to transpose the returned adjacency matrix. (Default: False)
        ctx : context, optional
            The context of returned adjacency matrix. (Default: cpu)
        scipy_fmt : str, optional
            If specified, return a scipy sparse matrix in the given format.
            Otherwise, return a backend dependent sparse tensor. (Default: None)
        etype : str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph. (Default: None)

        Returns
        -------
        SparseTensor or scipy.sparse.spmatrix
            Adjacency matrix.

        Examples
        --------

        Instantiate a heterogeneous graph.

        >>> follows_g = dgl.graph([(0, 0), (1, 1)], 'user', 'follows')
        >>> devs_g = dgl.bipartite([(0, 0), (1, 2)], 'developer', 'develops', 'game')
        >>> g = dgl.hetero_from_relations([follows_g, devs_g])

        Get a backend dependent sparse tensor. Here we use PyTorch for example.

        >>> g.adjacency_matrix(etype='develops')
        tensor(indices=tensor([[0, 2],
                               [0, 1]]),
               values=tensor([1., 1.]),
               size=(3, 2), nnz=2, layout=torch.sparse_coo)

        Get a scipy coo sparse matrix.

        >>> g.adjacency_matrix(scipy_fmt='coo', etype='develops')
        <3x2 sparse matrix of type '<class 'numpy.int64'>'
        with 2 stored elements in COOrdinate format>
        """
        if transpose is None:
            dgl_warning(
                "Currently adjacency_matrix() returns a matrix with destination as rows"
                " by default.  In 0.5 the result will have source as rows"
                " (i.e. transpose=True)")
            transpose = False

        etid = self.get_etype_id(etype)
        if scipy_fmt is None:
            return self._graph.adjacency_matrix(etid, transpose, ctx)[0]
        else:
            return self._graph.adjacency_matrix_scipy(etid, transpose, scipy_fmt, False)

    # Alias of ``adjacency_matrix``
    adj = adjacency_matrix

    def incidence_matrix(self, typestr, ctx=F.cpu(), etype=None):
        """Return the incidence matrix representation of edges with the given
        edge type.

        An incidence matrix is an n-by-m sparse matrix, where n is
        the number of nodes and m is the number of edges. Each nnz
        value indicating whether the edge is incident to the node
        or not.

        There are three types of incidence matrices :math:`I`:

        * ``in``:

            - :math:`I[v, e] = 1` if :math:`e` is the in-edge of :math:`v`
              (or :math:`v` is the dst node of :math:`e`);
            - :math:`I[v, e] = 0` otherwise.

        * ``out``:

            - :math:`I[v, e] = 1` if :math:`e` is the out-edge of :math:`v`
              (or :math:`v` is the src node of :math:`e`);
            - :math:`I[v, e] = 0` otherwise.

        * ``both`` (only if source and destination node type are the same):

            - :math:`I[v, e] = 1` if :math:`e` is the in-edge of :math:`v`;
            - :math:`I[v, e] = -1` if :math:`e` is the out-edge of :math:`v`;
            - :math:`I[v, e] = 0` otherwise (including self-loop).

        Parameters
        ----------
        typestr : str
            Can be either ``in``, ``out`` or ``both``
        ctx : context, optional
            The context of returned incidence matrix. (Default: cpu)
        etype : str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph.

        Returns
        -------
        Framework SparseTensor
            The incidence matrix.

        Examples
        --------

        >>> g = dgl.graph([(0, 0), (1, 2)], 'user', 'follows')
        >>> g.incidence_matrix('in')
        tensor(indices=tensor([[0, 2],
                               [0, 1]]),
               values=tensor([1., 1.]),
               size=(3, 2), nnz=2, layout=torch.sparse_coo)
        >>> g.incidence_matrix('out')
        tensor(indices=tensor([[0, 1],
                               [0, 1]]),
               values=tensor([1., 1.]),
               size=(3, 2), nnz=2, layout=torch.sparse_coo)
        >>> g.incidence_matrix('both')
        tensor(indices=tensor([[1, 2],
                               [1, 1]]),
               values=tensor([-1.,  1.]),
               size=(3, 2), nnz=2, layout=torch.sparse_coo)
        """
        etid = self.get_etype_id(etype)
        return self._graph.incidence_matrix(etid, typestr, ctx)[0]

    # Alias of ``incidence_matrix``
    inc = incidence_matrix

    #################################################################
    # Features
    #################################################################

    def node_attr_schemes(self, ntype=None):
        """Return the node feature schemes for the specified type.

        Each feature scheme is a named tuple that stores the shape and data type
        of the node feature.

        Parameters
        ----------
        ntype : str, optional
            The node type. Can be omitted if there is only one node
            type in the graph. Error will be raised otherwise.
            (Default: None)

        Returns
        -------
        dict of str to schemes
            The schemes of node feature columns.

        Examples
        --------
        The following uses PyTorch backend.

        >>> g = dgl.graph([(0, 0), (1, 2)], 'user', 'follows')
        >>> g.nodes['user'].data['h'] = torch.randn(3, 4)
        >>> g.node_attr_schemes('user')
        {'h': Scheme(shape=(4,), dtype=torch.float32)}

        See Also
        --------
        edge_attr_schemes
        """
        return self._node_frames[self.get_ntype_id(ntype)].schemes

    def edge_attr_schemes(self, etype=None):
        """Return the edge feature schemes for the specified type.

        Each feature scheme is a named tuple that stores the shape and data type
        of the edge feature.

        Parameters
        ----------
        etype : str or tuple of str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph. (Default: None)

        Returns
        -------
        dict of str to schemes
            The schemes of edge feature columns.

        Examples
        --------
        The following uses PyTorch backend.

        >>> g = dgl.bipartite([(0, 0), (1, 0), (1, 2), (2, 1)], 'user', 'plays', 'game')
        >>> g.edges['user', 'plays', 'game'].data['h'] = torch.randn(4, 4)
        >>> g.edge_attr_schemes(('user', 'plays', 'game'))
        {'h': Scheme(shape=(4,), dtype=torch.float32)}

        See Also
        --------
        node_attr_schemes
        """
        return self._edge_frames[self.get_etype_id(etype)].schemes

    def set_n_initializer(self, initializer, field=None, ntype=None):
        """Set the initializer for empty node features.

        Initializer is a callable that returns a tensor given the shape, data type
        and device context.

        When a subset of the nodes are assigned a new feature, initializer is
        used to create feature for the rest of the nodes.

        Parameters
        ----------
        initializer : callable
            The initializer, mapping (shape, data type, context) to tensor.
        field : str, optional
            The feature field name. Default is to set an initializer for all the
            feature fields.
        ntype : str, optional
            The node type. Can be omitted if there is only one node
            type in the graph. Error will be raised otherwise.
            (Default: None)

        Note
        -----
        User defined initializer must follow the signature of
        :func:`dgl.init.base_initializer() <dgl.init.base_initializer>`

        See Also
        --------
        set_e_initializer
        """
        ntid = self.get_ntype_id(ntype)
        self._node_frames[ntid].set_initializer(initializer, field)

    def set_e_initializer(self, initializer, field=None, etype=None):
        """Set the initializer for empty edge features.

        Initializer is a callable that returns a tensor given the shape, data
        type and device context.

        When a subset of the edges are assigned a new feature, initializer is
        used to create feature for rest of the edges.

        Parameters
        ----------
        initializer : callable
            The initializer, mapping (shape, data type, context) to tensor.
        field : str, optional
            The feature field name. Default is set an initializer for all the
            feature fields.
        etype : str or tuple of str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph. Error will be raised otherwise.
            (Default: None)

        Note
        -----
        User defined initializer must follow the signature of
        :func:`dgl.init.base_initializer() <dgl.init.base_initializer>`

        See Also
        --------
        set_n_initializer
        """
        etid = self.get_etype_id(etype)
        self._edge_frames[etid].set_initializer(initializer, field)

    def _set_n_repr(self, ntid, u, data, inplace=False):
        """Internal API to set node features.

        `data` is a dictionary from the feature name to feature tensor. Each tensor
        is of shape (B, D1, D2, ...), where B is the number of nodes to be updated,
        and (D1, D2, ...) be the shape of the node representation tensor. The
        length of the given node ids must match B (i.e, len(u) == B).

        All update will be done out of place to work with autograd unless the
        inplace flag is true.

        Parameters
        ----------
        ntid : int
            Node type id.
        u : node, container or tensor
            The node(s).
        data : dict of tensor
            Node representation.
        inplace : bool, optional
            If True, update will be done in place, but autograd will break.
            (Default: False)
        """
        if is_all(u):
            num_nodes = self._graph.number_of_nodes(ntid)
        else:
            u = utils.toindex(u)
            num_nodes = len(u)
        for key, val in data.items():
            nfeats = F.shape(val)[0]
            if nfeats != num_nodes:
                raise DGLError('Expect number of features to match number of nodes (len(u)).'
                               ' Got %d and %d instead.' % (nfeats, num_nodes))

        if is_all(u):
            for key, val in data.items():
                self._node_frames[ntid][key] = val
        else:
            self._node_frames[ntid].update_rows(u, data, inplace=inplace)

    def _get_n_repr(self, ntid, u):
        """Get node(s) representation of a single node type.

        The returned feature tensor batches multiple node features on the first dimension.

        Parameters
        ----------
        ntid : int
            Node type id.
        u : node, container or tensor
            The node(s).

        Returns
        -------
        dict
            Representation dict from feature name to feature tensor.
        """
        if is_all(u):
            return dict(self._node_frames[ntid])
        else:
            u = utils.toindex(u)
            return self._node_frames[ntid].select_rows(u)

    def _pop_n_repr(self, ntid, key):
        """Internal API to get and remove the specified node feature.

        Parameters
        ----------
        ntid : int
            Node type id.
        key : str
            The attribute name.

        Returns
        -------
        Tensor
            The popped representation
        """
        return self._node_frames[ntid].pop(key)

    def _set_e_repr(self, etid, edges, data, inplace=False):
        """Internal API to set edge(s) features.

        `data` is a dictionary from the feature name to feature tensor. Each tensor
        is of shape (B, D1, D2, ...), where B is the number of edges to be updated,
        and (D1, D2, ...) be the shape of the edge representation tensor.

        All update will be done out of place to work with autograd unless the
        inplace flag is true.

        Parameters
        ----------
        etid : int
            Edge type id.
        edges : edges
            Edges can be either

            * A pair of endpoint nodes (u, v), where u is the node ID of source
              node type and v is that of destination node type.
            * A tensor of edge ids of the given type.

            The default value is all the edges.
        data : tensor or dict of tensor
            Edge representation.
        inplace : bool, optional
            If True, update will be done in place, but autograd will break.
            (Default: False)
        """
        # parse argument
        if is_all(edges):
            eid = ALL
        elif isinstance(edges, tuple):
            u, v = edges
            u = utils.toindex(u)
            v = utils.toindex(v)
            # Rewrite u, v to handle edge broadcasting and multigraph.
            _, _, eid = self._graph.edge_ids(etid, u, v)
        else:
            eid = utils.toindex(edges)

        # sanity check
        if not utils.is_dict_like(data):
            raise DGLError('Expect dictionary type for feature data.'
                           ' Got "%s" instead.' % type(data))

        if is_all(eid):
            num_edges = self._graph.number_of_edges(etid)
        else:
            eid = utils.toindex(eid)
            num_edges = len(eid)
        for key, val in data.items():
            nfeats = F.shape(val)[0]
            if nfeats != num_edges:
                raise DGLError('Expect number of features to match number of edges.'
                               ' Got %d and %d instead.' % (nfeats, num_edges))
        # set
        if is_all(eid):
            # update column
            for key, val in data.items():
                self._edge_frames[etid][key] = val
        else:
            # update row
            self._edge_frames[etid].update_rows(eid, data, inplace=inplace)

    def _get_e_repr(self, etid, edges):
        """Internal API to get edge features.

        Parameters
        ----------
        etid : int
            Edge type id.
        edges : edges
            Edges can be a pair of endpoint nodes (u, v), or a
            tensor of edge ids. The default value is all the edges.

        Returns
        -------
        dict
            Representation dict
        """
        # parse argument
        if is_all(edges):
            eid = ALL
        elif isinstance(edges, tuple):
            u, v = edges
            u = utils.toindex(u)
            v = utils.toindex(v)
            # Rewrite u, v to handle edge broadcasting and multigraph.
            _, _, eid = self._graph.edge_ids(etid, u, v)
        else:
            eid = utils.toindex(edges)

        if is_all(eid):
            return dict(self._edge_frames[etid])
        else:
            eid = utils.toindex(eid)
            return self._edge_frames[etid].select_rows(eid)

    def _pop_e_repr(self, etid, key):
        """Get and remove the specified edge repr of a single edge type.

        Parameters
        ----------
        etid : int
            Edge type id.
        key : str
          The attribute name.

        Returns
        -------
        Tensor
            The popped representation
        """
        self._edge_frames[etid].pop(key)

    #################################################################
    # Message passing
    #################################################################

    def apply_nodes(self, func, v=ALL, ntype=None, inplace=False):
        """Apply the function on the nodes with the same type to update their
        features.

        If None is provided for ``func``, nothing will happen.

        Parameters
        ----------
        func : callable or None
            Apply function on the nodes. The function should be
            a :mod:`Node UDF <dgl.udf>`.
        v : int or iterable of int or tensor, optional
            The (type-specific) node (ids) on which to apply ``func``. (Default: ALL)
        ntype : str, optional
            The node type. Can be omitted if there is only one node type
            in the graph. (Default: None)
        inplace : bool, optional
            If True, update will be done in place, but autograd will break.
            (Default: False)

        Examples
        --------
        >>> g = dgl.graph([(0, 1), (1, 2)], 'user', 'follows')
        >>> g.nodes['user'].data['h'] = torch.ones(3, 5)
        >>> g.apply_nodes(lambda nodes: {'h': nodes.data['h'] * 2}, ntype='user')
        >>> g.nodes['user'].data['h']
        tensor([[2., 2., 2., 2., 2.],
                [2., 2., 2., 2., 2.],
                [2., 2., 2., 2., 2.]])

        See Also
        --------
        apply_edges
        """
        ntid = self.get_ntype_id(ntype)
        if is_all(v):
            v_ntype = utils.toindex(slice(0, self.number_of_nodes(ntype)))
        else:
            v_ntype = utils.toindex(v)
        with ir.prog() as prog:
            scheduler.schedule_apply_nodes(v_ntype, func, self._node_frames[ntid],
                                           inplace=inplace)
            Runtime.run(prog)

    def apply_edges(self, func, edges=ALL, etype=None, inplace=False):
        """Apply the function on the edges with the same type to update their
        features.

        If None is provided for ``func``, nothing will happen.

        Parameters
        ----------
        func : callable or None
            Apply function on the edge. The function should be
            an :mod:`Edge UDF <dgl.udf>`.
        edges : optional
            Edges on which to apply ``func``. See :func:`send` for valid
            edge specification. (Default: ALL)
        etype : str or tuple of str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph. (Default: None)
        inplace: bool, optional
            If True, update will be done in place, but autograd will break.
            (Default: False)

        Examples
        --------
        >>> g = dgl.bipartite([(0, 0), (1, 0), (1, 2), (2, 1)], 'user', 'plays', 'game')
        >>> g.edges[('user', 'plays', 'game')].data['h'] = torch.ones(4, 5)
        >>> g.apply_edges(lambda edges: {'h': edges.data['h'] * 2})
        >>> g.edges[('user', 'plays', 'game')].data['h']
        tensor([[2., 2., 2., 2., 2.],
                [2., 2., 2., 2., 2.],
                [2., 2., 2., 2., 2.],
                [2., 2., 2., 2., 2.]])

        See Also
        --------
        apply_nodes
        group_apply_edges
        """
        etid = self.get_etype_id(etype)
        stid, dtid = self._graph.metagraph.find_edge(etid)
        if is_all(edges):
            u, v, _ = self._graph.edges(etid, 'eid')
            eid = utils.toindex(slice(0, self.number_of_edges(etype)))
        elif isinstance(edges, tuple):
            u, v = edges
            u = utils.toindex(u)
            v = utils.toindex(v)
            # Rewrite u, v to handle edge broadcasting and multigraph.
            u, v, eid = self._graph.edge_ids(etid, u, v)
        else:
            eid = utils.toindex(edges)
            u, v, _ = self._graph.find_edges(etid, eid)

        with ir.prog() as prog:
            scheduler.schedule_apply_edges(
                AdaptedHeteroGraph(self, stid, dtid, etid),
                u, v, eid, func, inplace=inplace)
            Runtime.run(prog)

    def group_apply_edges(self, group_by, func, edges=ALL, etype=None, inplace=False):
        """Group the edges by nodes and apply the function of the grouped
        edges to update their features.  The edges are of the same edge type
        (hence having the same source and destination node type).

        Parameters
        ----------
        group_by : str
            Specify how to group edges. Expected to be either ``'src'`` or ``'dst'``
        func : callable
            Apply function on the edge. The function should be an
            :mod:`Edge UDF <dgl.udf>`. The input of `Edge UDF` should be
            (bucket_size, degrees, *feature_shape), and return the dict
            with values of the same shapes.
        edges : optional
            Edges on which to group and apply ``func``. See :func:`send` for valid
            edge specification. Default is all the edges.
        etype : str or tuple of str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph. (Default: None)
        inplace: bool, optional
            If True, update will be done in place, but autograd will break.
            (Default: False)

        Examples
        --------
        >>> g = dgl.graph([(0, 1), (0, 2), (1, 2)], 'user', 'follows')
        >>> g.edata['feat'] = torch.randn((g.number_of_edges(), 1))
        >>> def softmax_feat(edges):
        >>>     return {'norm_feat': th.softmax(edges.data['feat'], dim=1)}
        >>> g.group_apply_edges(group_by='src', func=softmax_feat)
        >>> g.edata['norm_feat']
        tensor([[0.3796],
                [0.6204],
                [1.0000]])

        See Also
        --------
        apply_edges
        """
        if group_by not in ('src', 'dst'):
            raise DGLError("Group_by should be either src or dst")

        etid = self.get_etype_id(etype)
        stid, dtid = self._graph.metagraph.find_edge(etid)
        if is_all(edges):
            u, v, _ = self._graph.edges(etid, 'eid')
            eid = utils.toindex(slice(0, self.number_of_edges(etype)))
        elif isinstance(edges, tuple):
            u, v = edges
            u = utils.toindex(u)
            v = utils.toindex(v)
            # Rewrite u, v to handle edge broadcasting and multigraph.
            u, v, eid = self._graph.edge_ids(etid, u, v)
        else:
            eid = utils.toindex(edges)
            u, v, _ = self._graph.find_edges(etid, eid)

        with ir.prog() as prog:
            scheduler.schedule_group_apply_edge(
                AdaptedHeteroGraph(self, stid, dtid, etid),
                u, v, eid,
                func, group_by,
                inplace=inplace)
            Runtime.run(prog)

    def send(self, edges, message_func, etype=None):
        """Send messages along the given edges with the same edge type.

        ``edges`` can be any of the following types:

        * ``int`` : Specify one edge using its edge id (of the given edge type).
        * ``pair of int`` : Specify one edge using its endpoints (of source node type
          and destination node type respectively).
        * ``int iterable`` / ``tensor`` : Specify multiple edges using their edge ids.
        * ``pair of int iterable`` / ``pair of tensors`` :
          Specify multiple edges using their endpoints.

        **Only works if the graph has one edge type.** For multiple types, use

        .. code::

           g['edgetype'].send(edges, message_func)

        The UDF returns messages on the edges and can be later fetched in
        the destination node's ``mailbox``. Receiving will consume the messages.
        See :func:`recv` for example.

        If multiple ``send`` are triggered on the same edge without ``recv``. Messages
        generated by the later ``send`` will overwrite previous messages.

        Parameters
        ----------
        edges : optional
            Edges on which to apply ``message_func``.
        message_func : callable
            Message function on the edges. The function should be
            an :mod:`Edge UDF <dgl.udf>`.

        Notes
        -----
        On multigraphs, if :math:`u` and :math:`v` are specified, then the messages will be sent
        along all edges between :math:`u` and :math:`v`.

        Examples
        --------

        >>> import dgl.function as fn
        >>> import torch
        >>> g = dgl.graph([(0, 1), (1, 2)], 'user', 'follows')
        >>> g.nodes['user'].data['h'] = torch.tensor([[0.], [1.], [2.]])

        Different ways for sending messages.

        >>> # Send the feature of source nodes along all edges
        >>> g.send(g.edges(), fn.copy_src('h', 'm'))
        >>> # Send the feature of source node along one edge specified by its id
        >>> g.send(0, fn.copy_src('h', 'm'))
        >>> # Send the feature of source node along one edge specified by its end points
        >>> g.send((0, 1), fn.copy_src('h', 'm'))
        >>> # Send the feature of source nodes along multiple edges specified by their ids
        >>> g.send([0, 1], fn.copy_src('h', 'm'))
        >>> # Send the feature of source nodes along multiple edges specified by their end points
        >>> g.send(([0, 1], [1, 2]), fn.copy_src('h', 'm'))
        """
        assert message_func is not None
        etid = self.get_etype_id(etype)
        stid, dtid = self._graph.metagraph.find_edge(etid)

        if is_all(edges):
            eid = utils.toindex(slice(0, self._graph.number_of_edges(etid)))
            u, v, _ = self._graph.edges(etid, 'eid')
        elif isinstance(edges, tuple):
            u, v = edges
            u = utils.toindex(u)
            v = utils.toindex(v)
            # Rewrite u, v to handle edge broadcasting and multigraph.
            u, v, eid = self._graph.edge_ids(etid, u, v)
        else:
            eid = utils.toindex(edges)
            u, v, _ = self._graph.find_edges(etid, eid)

        if len(eid) == 0:
            # no edge to be triggered
            return

        with ir.prog() as prog:
            scheduler.schedule_send(
                AdaptedHeteroGraph(self, stid, dtid, etid),
                u, v, eid,
                message_func)
            Runtime.run(prog)

    def recv(self,
             v,
             reduce_func,
             apply_node_func=None,
             etype=None,
             inplace=False):
        r"""Receive and reduce incoming messages and update the features of node(s) :math:`v`.

        It calculates:

        .. math::
            h_v^{new} = \sigma(f(\{m_{uv} | u\in\mathcal{N}_{t}(v)\}))

        where :math:`\mathcal{N}_t(v)` defines the predecessors of node(s) :math:`v` connected by
        edges of type :math:`t`, and :math:`m_{uv}` is the message on edge :math:`(u,v)`.

        * ``reduce_func`` specifies :math:`f`, e.g. summation or average.
        * ``apply_node_func`` specifies :math:`\sigma`, e.g. ReLU activation.

        Other notes:

        * `reduce_func` will be skipped for nodes with no incoming message.
        * If all ``v`` have no incoming message, this will downgrade to an :func:`apply_nodes`.
        * If some ``v`` have no incoming message, their new feature value will be calculated
          by the column initializer (see :func:`set_n_initializer`). The feature shapes and
          dtypes will be inferred.
        * The node features will be updated by the result of the ``reduce_func``.
        * Messages are consumed once received.
        * The provided UDF may be called multiple times so it is recommended to provide
          function with no side effect.

        Parameters
        ----------
        v : int, container or tensor
            The node(s) to be updated.
        reduce_func : callable
            Reduce function on the node. The function should be
            a :mod:`Node UDF <dgl.udf>`.
        apply_node_func : callable
            Apply function on the nodes. The function should be
            a :mod:`Node UDF <dgl.udf>`. (Default: None)
        etype : str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph. (Default: None)
        inplace: bool, optional
            If True, update will be done in place, but autograd will break.
            (Default: False)

        Examples
        --------

        >>> import dgl
        >>> import dgl.function as fn
        >>> import torch

        Instantiate a heterograph.

        >>> follows_g = dgl.graph([(0, 1), (1, 2)], 'user', 'follows')
        >>> plays_g = dgl.bipartite([(0, 0), (1, 0), (1, 1), (2, 1)], 'user', 'plays', 'game')
        >>> g = dgl.hetero_from_relations([follows_g, plays_g])
        >>> g.nodes['user'].data['h'] = torch.tensor([[0.], [1.], [2.]])

        Send and receive.

        >>> g.send(g['follows'].edges(), fn.copy_src('h', 'm'), etype='follows')
        >>> g.recv(g.nodes('user'), fn.sum('m', 'h'), etype='follows')
        >>> g.nodes['user'].data['h']
        tensor([[0.],
                [0.],
                [1.]])
        """
        etid = self.get_etype_id(etype)
        stid, dtid = self._graph.metagraph.find_edge(etid)
        if is_all(v):
            v = F.arange(0, self.number_of_nodes(dtid))
        elif isinstance(v, int):
            v = [v]
        v = utils.toindex(v)
        if len(v) == 0:
            # no vertex to be triggered.
            return
        with ir.prog() as prog:
            scheduler.schedule_recv(AdaptedHeteroGraph(self, stid, dtid, etid),
                                    v, reduce_func, apply_node_func,
                                    inplace=inplace)
            Runtime.run(prog)

    def multi_recv(self, v, reducer_dict, cross_reducer, apply_node_func=None, inplace=False):
        r"""Receive messages from multiple edge types and perform aggregation.

        It calculates:

        .. math::

            \begin{align}
            h_{v, t}^{new} &= f\left(\left\{m_{uv} | u\in\mathcal{N}_{t}(v)\right\}\right)\\
            h_v^{new} &= \sigma\left(g\left(\left\{h_{v, t}^{new} | t\in T_e\right\}\right)\right)
            \end{align}

        * ``per_type_reducer`` is a dictionary mapping edge type (str or tuple of str) to
          reduce functions :math:`f` of each type.
        * ``cross_reducer`` specifies :math:`g`.
        * ``apply_node_func`` specifies :math:`\sigma`.

        Parameters
        ----------
        v : int, container or tensor
            The node(s) to be updated.
        reducer_dict : dict of callable
            Mapping edge type (str or tuple of str) to reduce function (:mod:`Node UDF <dgl.udf>`).
        cross_reducer : str
            Cross type reducer. One of ``"sum"``, ``"min"``, ``"max"``, ``"mean"``, ``"stack"``.
        apply_node_func : callable
            Apply function on the nodes. The function should be
            a :mod:`Node UDF <dgl.udf>`. (Default: None)
        inplace: bool, optional
            If True, update will be done in place, but autograd will break.
            (Default: False)

        Examples
        --------

        >>> import dgl
        >>> import dgl.function as fn
        >>> import torch

        Instantiate a heterograph.

        >>> g1 = dgl.graph([(0, 1)], 'user', 'follows')
        >>> g2 = dgl.bipartite([(0, 1)], 'game', 'attracts', 'user')
        >>> g = dgl.hetero_from_relations([g1, g2])
        >>> g.nodes['user'].data['h'] = torch.tensor([[1.], [2.]])
        >>> g.nodes['game'].data['h'] = torch.tensor([[1.]])

        Send and receive.

        >>> g.send(g['follows'].edges(), fn.copy_src('h', 'm'), etype='follows')
        >>> g.send(g['attracts'].edges(), fn.copy_src('h', 'm'), etype='attracts')
        >>> g.multi_recv(g.nodes('user'), {'follows': fn.sum('m', 'h'),
        >>>              'attracts': fn.sum('m', 'h')}, "sum")
        >>> g.nodes['user'].data['h']
        tensor([[0.],
                [2.]])
        """
        # infer receive node type
        ntype = infer_ntype_from_dict(self, reducer_dict)
        ntid = self.get_ntype_id(ntype)
        if is_all(v):
            v = F.arange(0, self.number_of_nodes(ntid))
        elif isinstance(v, int):
            v = [v]
        v = utils.toindex(v)
        if len(v) == 0:
            return
        # TODO(minjie): currently loop over each edge type and reuse the old schedule.
        #   Should replace it with fused kernel.
        all_out = []
        with ir.prog() as prog:
            for ety, args in reducer_dict.items():
                outframe = FrameRef(frame_like(self._node_frames[ntid]._frame))
                args = pad_tuple(args, 2)
                if args is None:
                    raise DGLError('Invalid per-type arguments. Should be either '
                                   '(1) reduce_func or (2) (reduce_func, apply_node_func)')
                rfunc, afunc = args
                etid = self.get_etype_id(ety)
                stid, dtid = self._graph.metagraph.find_edge(etid)
                scheduler.schedule_recv(AdaptedHeteroGraph(self, stid, dtid, etid),
                                        v, rfunc, afunc,
                                        inplace=inplace, outframe=outframe)
                all_out.append(outframe)
            Runtime.run(prog)
        # merge by cross_reducer
        self._node_frames[ntid].update(merge_frames(all_out, cross_reducer))
        # apply
        if apply_node_func is not None:
            self.apply_nodes(apply_node_func, v, ntype, inplace)

    def send_and_recv(self,
                      edges,
                      message_func,
                      reduce_func,
                      apply_node_func=None,
                      etype=None,
                      inplace=False):
        """Send messages along edges of the specified type, and let destinations
        receive them.

        Optionally, apply a function to update the node features after "receive".

        This is a convenient combination for performing
        :mod:`send <dgl.DGLHeteroGraph.send>` along the ``edges`` and
        :mod:`recv <dgl.DGLHeteroGraph.recv>` for the destinations of the ``edges``.

        **Only works if the graph has one edge type.**  For multiple types, use

        .. code::

           g['edgetype'].send_and_recv(edges, message_func, reduce_func,
                                       apply_node_func, inplace=inplace)

        Parameters
        ----------
        edges : See :func:`send` for valid edge specification.
            Edges on which to apply ``func``.
        message_func : callable
            Message function on the edges. The function should be
            an :mod:`Edge UDF <dgl.udf>`.
        reduce_func : callable
            Reduce function on the node. The function should be
            a :mod:`Node UDF <dgl.udf>`.
        apply_node_func : callable, optional
            Apply function on the nodes. The function should be
            a :mod:`Node UDF <dgl.udf>`. (Default: None)
        etype : str or tuple of str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph. (Default: None)
        inplace: bool, optional
            If True, update will be done in place, but autograd will break.
            (Default: False)

        Examples
        --------

        >>> import dgl
        >>> import dgl.function as fn
        >>> import torch

        Instantiate a heterograph.

        >>> follows_g = dgl.graph([(0, 1), (1, 2)], 'user', 'follows')
        >>> plays_g = dgl.bipartite([(0, 0), (1, 0), (1, 1), (2, 1)], 'user', 'plays', 'game')
        >>> g = dgl.hetero_from_relations([follows_g, plays_g])

        Trigger "send" and "receive" separately.

        >>> g.nodes['user'].data['h'] = torch.tensor([[0.], [1.], [2.]])
        >>> g.send(g['follows'].edges(), fn.copy_src('h', 'm'), etype='follows')
        >>> g.recv(g.nodes('user'), fn.sum('m', 'h'), etype='follows')
        >>> g.nodes['user'].data['h']
        tensor([[0.],
                [0.],
                [1.]])

        Trigger "send" and "receive" in one call.

        >>> g.nodes['user'].data['h'] = torch.tensor([[0.], [1.], [2.]])
        >>> g.send_and_recv(g['follows'].edges(), fn.copy_src('h', 'm'),
        >>>                 fn.sum('m', 'h'), etype='follows')
        >>> g.nodes['user'].data['h']
        tensor([[0.],
                [0.],
                [1.]])
        """
        etid = self.get_etype_id(etype)
        stid, dtid = self._graph.metagraph.find_edge(etid)

        if isinstance(edges, tuple):
            u, v = edges
            u = utils.toindex(u)
            v = utils.toindex(v)
            # Rewrite u, v to handle edge broadcasting and multigraph.
            u, v, eid = self._graph.edge_ids(etid, u, v)
        else:
            eid = utils.toindex(edges)
            u, v, _ = self._graph.find_edges(etid, eid)

        if len(u) == 0:
            # no edges to be triggered
            return

        with ir.prog() as prog:
            scheduler.schedule_snr(AdaptedHeteroGraph(self, stid, dtid, etid),
                                   (u, v, eid),
                                   message_func, reduce_func, apply_node_func,
                                   inplace=inplace)
            Runtime.run(prog)

    def multi_send_and_recv(self, etype_dict, cross_reducer, apply_node_func=None, inplace=False):
        r"""Send and receive messages along multiple edge types and perform aggregation.

        Optionally, apply a function to update the node features after "receive".

        This is a convenient combination for performing multiple
        :mod:`send <dgl.DGLHeteroGraph.send>` along edges of different types and
        :mod:`multi_recv <dgl.DGLHeteroGraph.multi_recv>` for the destinations of all edges.

        Parameters
        ----------
        etype_dict : dict
            Mapping an edge type (str or tuple of str) to the type specific
            configuration (4-tuples). Each 4-tuple represents
            (edges, msg_func, reduce_func, apply_node_func):

            * edges: See send() for valid edge specification.
                  Edges on which to pass messages.
            * msg_func: callable
                  Message function on the edges. The function should be
                  an :mod:`Edge UDF <dgl.udf>`.
            * reduce_func: callable
                  Reduce function on the node. The function should be
                  a :mod:`Node UDF <dgl.udf>`.
            * apply_node_func : callable, optional
                  Apply function on the nodes. The function should be
                  a :mod:`Node UDF <dgl.udf>`. (Default: None)
        cross_reducer : str
            Cross type reducer. One of ``"sum"``, ``"min"``, ``"max"``, ``"mean"``, ``"stack"``.
        apply_node_func : callable
            Apply function on the nodes. The function should be
            a :mod:`Node UDF <dgl.udf>`. (Default: None)
        inplace: bool, optional
            If True, update will be done in place, but autograd will break.
            (Default: False)

        Examples
        --------

        >>> import dgl
        >>> import dgl.function as fn
        >>> import torch

        Instantiate a heterograph.

        >>> g1 = dgl.graph([(0, 1)], 'user', 'follows')
        >>> g2 = dgl.bipartite([(0, 1)], 'game', 'attracts', 'user')
        >>> g = dgl.hetero_from_relations([g1, g2])

        Trigger send and recv separately.

        >>> g.nodes['user'].data['h'] = torch.tensor([[1.], [2.]])
        >>> g.nodes['game'].data['h'] = torch.tensor([[1.]])
        >>> g.send(g['follows'].edges(), fn.copy_src('h', 'm'), etype='follows')
        >>> g.send(g['attracts'].edges(), fn.copy_src('h', 'm'), etype='attracts')
        >>> g.multi_recv(g.nodes('user'),
        >>>              {'follows': fn.sum('m', 'h'), 'attracts': fn.sum('m', 'h')}, "sum")
        >>> g.nodes['user'].data['h']
        tensor([[0.],
                [2.]])

        Trigger “send” and “receive” in one call.

        >>> g.nodes['user'].data['h'] = torch.tensor([[1.], [2.]])
        >>> g.nodes['game'].data['h'] = torch.tensor([[1.]])
        >>> g.multi_send_and_recv(
        >>>     {'follows': (g['follows'].edges(), fn.copy_src('h', 'm'), fn.sum('m', 'h')),
        >>>      'attracts': (g['attracts'].edges(), fn.copy_src('h', 'm'), fn.sum('m', 'h'))},
        >>> "sum")
        >>> g.nodes['user'].data['h']
        tensor([[0.],
                [2.]])
        """
        # infer receive node type
        ntype = infer_ntype_from_dict(self, etype_dict)
        dtid = self.get_ntype_id(ntype)

        # TODO(minjie): currently loop over each edge type and reuse the old schedule.
        #   Should replace it with fused kernel.
        all_out = []
        all_vs = []
        with ir.prog() as prog:
            for etype, args in etype_dict.items():
                etid = self.get_etype_id(etype)
                stid, _ = self._graph.metagraph.find_edge(etid)
                outframe = FrameRef(frame_like(self._node_frames[dtid]._frame))
                args = pad_tuple(args, 4)
                if args is None:
                    raise DGLError('Invalid per-type arguments. Should be '
                                   '(edges, msg_func, reduce_func, [apply_node_func])')
                edges, mfunc, rfunc, afunc = args
                if isinstance(edges, tuple):
                    u, v = edges
                    u = utils.toindex(u)
                    v = utils.toindex(v)
                    # Rewrite u, v to handle edge broadcasting and multigraph.
                    u, v, eid = self._graph.edge_ids(etid, u, v)
                else:
                    eid = utils.toindex(edges)
                    u, v, _ = self._graph.find_edges(etid, eid)
                all_vs.append(v)
                if len(u) == 0:
                    # no edges to be triggered
                    continue
                scheduler.schedule_snr(AdaptedHeteroGraph(self, stid, dtid, etid),
                                       (u, v, eid),
                                       mfunc, rfunc, afunc,
                                       inplace=inplace, outframe=outframe)
                all_out.append(outframe)
            Runtime.run(prog)
        # merge by cross_reducer
        self._node_frames[dtid].update(merge_frames(all_out, cross_reducer))
        # apply
        if apply_node_func is not None:
            dstnodes = F.unique(F.cat([x.tousertensor() for x in all_vs], 0))
            self.apply_nodes(apply_node_func, dstnodes, ntype, inplace)

    def pull(self,
             v,
             message_func,
             reduce_func,
             apply_node_func=None,
             etype=None,
             inplace=False):
        """Pull messages from the node(s)' predecessors and then update their features.

        Optionally, apply a function to update the node features after receive.

        This is equivalent to :mod:`send_and_recv <dgl.DGLHeteroGraph.send_and_recv>`
        on the incoming edges of ``v`` with the specified type.

        Other notes:

        * `reduce_func` will be skipped for nodes with no incoming messages.
        * If all ``v`` have no incoming message, this will downgrade to an :func:`apply_nodes`.
        * If some ``v`` have no incoming message, their new feature value will be calculated
          by the column initializer (see :func:`set_n_initializer`). The feature shapes and
          dtypes will be inferred.

        **Only works if the graph has one edge type.** For multiple types, use

        .. code::

           g['edgetype'].pull(v, message_func, reduce_func, apply_node_func, inplace=inplace)

        Parameters
        ----------
        v : int, container or tensor, optional
            The node(s) to be updated.
        message_func : callable
            Message function on the edges. The function should be
            an :mod:`Edge UDF <dgl.udf>`.
        reduce_func : callable
            Reduce function on the node. The function should be
            a :mod:`Node UDF <dgl.udf>`.
        apply_node_func : callable, optional
            Apply function on the nodes. The function should be
            a :mod:`Node UDF <dgl.udf>`. (Default: None)
        etype : str or tuple of str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph. (Default: None)
        inplace: bool, optional
            If True, update will be done in place, but autograd will break.
            (Default: False)

        Examples
        --------

        >>> import dgl
        >>> import dgl.function as fn
        >>> import torch

        Instantiate a heterograph.

        >>> follows_g = dgl.graph([(0, 1), (1, 2)], 'user', 'follows')
        >>> plays_g = dgl.bipartite([(0, 0), (2, 1)], 'user', 'plays', 'game')
        >>> g = dgl.hetero_from_relations([follows_g, plays_g])
        >>> g.nodes['user'].data['h'] = torch.tensor([[0.], [1.], [2.]])

        Pull.

        >>> g['follows'].pull(2, fn.copy_src('h', 'm'), fn.sum('m', 'h'), etype='follows')
        >>> g.nodes['user'].data['h']
        tensor([[0.],
                [1.],
                [1.]])
        """
        # only one type of edges
        etid = self.get_etype_id(etype)
        stid, dtid = self._graph.metagraph.find_edge(etid)

        v = utils.toindex(v)
        if len(v) == 0:
            return
        with ir.prog() as prog:
            scheduler.schedule_pull(AdaptedHeteroGraph(self, stid, dtid, etid),
                                    v,
                                    message_func, reduce_func, apply_node_func,
                                    inplace=inplace)
            Runtime.run(prog)

    def multi_pull(self, v, etype_dict, cross_reducer, apply_node_func=None, inplace=False):
        r"""Pull and receive messages of the given nodes along multiple edge types
        and perform aggregation.

        This is equivalent to :mod:`multi_send_and_recv <dgl.DGLHeteroGraph.multi_send_and_recv>`
        on the incoming edges of ``v`` with the specified types.

        Parameters
        ----------
        v : int, container or tensor
            The node(s) to be updated.
        etype_dict : dict
            Mapping an edge type (str or tuple of str) to the type specific
            configuration (3-tuples). Each 3-tuple represents
            (msg_func, reduce_func, apply_node_func):

            * msg_func: callable
                  Message function on the edges. The function should be
                  an :mod:`Edge UDF <dgl.udf>`.
            * reduce_func: callable
                  Reduce function on the nodes. The function should be
                  a :mod:`Node UDF <dgl.udf>`.
            * apply_node_func : callable, optional
                  Apply function on the nodes. The function should be
                  a :mod:`Node UDF <dgl.udf>`. (Default: None)
        cross_reducer : str
            Cross type reducer. One of ``"sum"``, ``"min"``, ``"max"``, ``"mean"``, ``"stack"``.
        apply_node_func : callable
            Apply function on the nodes. The function should be
            a :mod:`Node UDF <dgl.udf>`. (Default: None)
        inplace: bool, optional
            If True, update will be done in place, but autograd will break.
            (Default: False)

        Examples
        --------

        >>> import dgl
        >>> import dgl.function as fn
        >>> import torch

        Instantiate a heterograph.

        >>> g1 = dgl.graph([(1, 1), (1, 0)], 'user', 'follows')
        >>> g2 = dgl.bipartite([(0, 1)], 'game', 'attracts', 'user')
        >>> g = dgl.hetero_from_relations([g1, g2])

        Pull.

        >>> g.nodes['user'].data['h'] = torch.tensor([[1.], [2.]])
        >>> g.nodes['game'].data['h'] = torch.tensor([[1.]])
        >>> g.multi_pull(1,
        >>>              {'follows': (fn.copy_src('h', 'm'), fn.sum('m', 'h')),
        >>>               'attracts': (fn.copy_src('h', 'm'), fn.sum('m', 'h'))},
        >>> "sum")
        >>> g.nodes['user'].data['h']
        tensor([[0.],
                [3.]])
        """
        v = utils.toindex(v)
        if len(v) == 0:
            return
        # infer receive node type
        ntype = infer_ntype_from_dict(self, etype_dict)
        dtid = self.get_ntype_id(ntype)
        # TODO(minjie): currently loop over each edge type and reuse the old schedule.
        #   Should replace it with fused kernel.
        all_out = []
        with ir.prog() as prog:
            for etype, args in etype_dict.items():
                etid = self.get_etype_id(etype)
                stid, _ = self._graph.metagraph.find_edge(etid)
                outframe = FrameRef(frame_like(self._node_frames[dtid]._frame))
                args = pad_tuple(args, 3)
                if args is None:
                    raise DGLError('Invalid per-type arguments. Should be '
                                   '(msg_func, reduce_func, [apply_node_func])')
                mfunc, rfunc, afunc = args
                scheduler.schedule_pull(AdaptedHeteroGraph(self, stid, dtid, etid),
                                        v,
                                        mfunc, rfunc, afunc,
                                        inplace=inplace, outframe=outframe)
                all_out.append(outframe)
            Runtime.run(prog)
        # merge by cross_reducer
        self._node_frames[dtid].update(merge_frames(all_out, cross_reducer))
        # apply
        if apply_node_func is not None:
            self.apply_nodes(apply_node_func, v, ntype, inplace)

    def push(self,
             u,
             message_func,
             reduce_func,
             apply_node_func=None,
             etype=None,
             inplace=False):
        """Send message from the node(s) to their successors and update them.

        This is equivalent to performing
        :mod:`send_and_recv <DGLHeteroGraph.send_and_recv>` along the outbound
        edges from ``u``.

        **Only works if the graph has one edge type.** For multiple types, use

        .. code::

           g['edgetype'].push(u, message_func, reduce_func, apply_node_func, inplace=inplace)

        Parameters
        ----------
        u : int, container or tensor
            The node(s) to push out messages.
        message_func : callable
            Message function on the edges. The function should be
            an :mod:`Edge UDF <dgl.udf>`.
        reduce_func : callable
            Reduce function on the node. The function should be
            a :mod:`Node UDF <dgl.udf>`.
        apply_node_func : callable, optional
            Apply function on the nodes. The function should be
            a :mod:`Node UDF <dgl.udf>`. (Default: None)
        etype : str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph. (Default: None)
        inplace: bool, optional
            If True, update will be done in place, but autograd will break.
            (Default: False)

        Examples
        --------

        >>> import dgl
        >>> import dgl.function as fn
        >>> import torch

        Instantiate a heterograph.

        >>> g = dgl.graph([(0, 1), (0, 2)], 'user', 'follows')
        >>> g.nodes['user'].data['h'] = torch.tensor([[0.], [1.], [2.]])

        Push.

        >>> g['follows'].push(0, fn.copy_src('h', 'm'), fn.sum('m', 'h'), etype='follows')
        >>> g.nodes['user'].data['h']
        tensor([[0.],
                [0.],
                [0.]])
        """
        # only one type of edges
        etid = self.get_etype_id(etype)
        stid, dtid = self._graph.metagraph.find_edge(etid)

        u = utils.toindex(u)
        if len(u) == 0:
            return
        with ir.prog() as prog:
            scheduler.schedule_push(AdaptedHeteroGraph(self, stid, dtid, etid),
                                    u,
                                    message_func, reduce_func, apply_node_func,
                                    inplace=inplace)
            Runtime.run(prog)

    def update_all(self,
                   message_func,
                   reduce_func,
                   apply_node_func=None,
                   etype=None):
        """Send messages through all edges and update all nodes.

        Optionally, apply a function to update the node features after receive.

        This is equivalent to
        :mod:`send_and_recv <dgl.DGLHeteroGraph.send_and_recv>` over all edges
        of the specified type.

        **Only works if the graph has one edge type.** For multiple types, use

        .. code::

           g['edgetype'].update_all(message_func, reduce_func, apply_node_func)

        Parameters
        ----------
        message_func : callable
            Message function on the edges. The function should be
            an :mod:`Edge UDF <dgl.udf>`.
        reduce_func : callable
            Reduce function on the node. The function should be
            a :mod:`Node UDF <dgl.udf>`.
        apply_node_func : callable, optional
            Apply function on the nodes. The function should be
            a :mod:`Node UDF <dgl.udf>`. (Default: None)
        etype : str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph. (Default: None)

        Examples
        --------
        >>> import torch
        >>> import dgl
        >>> import dgl.function as fn

        Instantiate a heterograph.

        >>> g = dgl.graph([(0, 1), (1, 2), (2, 2)], 'user', 'follows')

        Update all.

        >>> g.nodes['user'].data['h'] = torch.tensor([[0.], [1.], [2.]])
        >>> g['follows'].update_all(fn.copy_src('h', 'm'), fn.sum('m', 'h'), etype='follows')
        >>> g.nodes['user'].data['h']
        tensor([[0.],
                [0.],
                [3.]])
        """
        # only one type of edges
        etid = self.get_etype_id(etype)
        stid, dtid = self._graph.metagraph.find_edge(etid)

        with ir.prog() as prog:
            scheduler.schedule_update_all(AdaptedHeteroGraph(self, stid, dtid, etid),
                                          message_func, reduce_func,
                                          apply_node_func)
            Runtime.run(prog)

    def multi_update_all(self, etype_dict, cross_reducer, apply_node_func=None):
        r"""Send and receive messages along all edges.

        This is equivalent to
        :mod:`multi_send_and_recv <dgl.DGLHeteroGraph.multi_send_and_recv>`
        over all edges.

        Parameters
        ----------
        etype_dict : dict
            Mapping an edge type (str or tuple of str) to the type specific
            configuration (3-tuples). Each 3-tuple represents
            (msg_func, reduce_func, apply_node_func):

            * msg_func: callable
                  Message function on the edges. The function should be
                  an :mod:`Edge UDF <dgl.udf>`.
            * reduce_func: callable
                  Reduce function on the nodes. The function should be
                  a :mod:`Node UDF <dgl.udf>`.
            * apply_node_func : callable, optional
                  Apply function on the nodes. The function should be
                  a :mod:`Node UDF <dgl.udf>`. (Default: None)
        cross_reducer : str
            Cross type reducer. One of ``"sum"``, ``"min"``, ``"max"``, ``"mean"``, ``"stack"``.
        apply_node_func : callable
            Apply function on the nodes. The function should be
            a :mod:`Node UDF <dgl.udf>`. (Default: None)
        inplace: bool, optional
            If True, update will be done in place, but autograd will break.
            (Default: False)

        etype_dict : dict of callable
            ``update_all`` arguments per edge type.

        Examples
        --------
        >>> import dgl
        >>> import dgl.function as fn
        >>> import torch

        Instantiate a heterograph.

        >>> g1 = dgl.graph([(0, 1), (1, 1)], 'user', 'follows')
        >>> g2 = dgl.bipartite([(0, 1)], 'game', 'attracts', 'user')
        >>> g = dgl.hetero_from_relations([g1, g2])
        >>> g.nodes['user'].data['h'] = torch.tensor([[1.], [2.]])
        >>> g.nodes['game'].data['h'] = torch.tensor([[1.]])

        Update all.

        >>> g.multi_update_all(
        >>>     {'follows': (fn.copy_src('h', 'm'), fn.sum('m', 'h')),
        >>>      'attracts': (fn.copy_src('h', 'm'), fn.sum('m', 'h'))},
        >>> "sum")
        >>> g.nodes['user'].data['h']
        tensor([[0.],
                [4.]])
        """
        # TODO(minjie): currently loop over each edge type and reuse the old schedule.
        #   Should replace it with fused kernel.
        all_out = defaultdict(list)
        with ir.prog() as prog:
            for etype, args in etype_dict.items():
                etid = self.get_etype_id(etype)
                stid, dtid = self._graph.metagraph.find_edge(etid)
                outframe = FrameRef(frame_like(self._node_frames[dtid]._frame))
                args = pad_tuple(args, 3)
                if args is None:
                    raise DGLError('Invalid per-type arguments. Should be '
                                   '(msg_func, reduce_func, [apply_node_func])')
                mfunc, rfunc, afunc = args
                scheduler.schedule_update_all(AdaptedHeteroGraph(self, stid, dtid, etid),
                                              mfunc, rfunc, afunc,
                                              outframe=outframe)
                all_out[dtid].append(outframe)
            Runtime.run(prog)
        for dtid, frames in all_out.items():
            # merge by cross_reducer
            self._node_frames[dtid].update(merge_frames(frames, cross_reducer))
            # apply
            if apply_node_func is not None:
                self.apply_nodes(apply_node_func, ALL, self.ntypes[dtid], inplace=False)

    def prop_nodes(self,
                   nodes_generator,
                   message_func,
                   reduce_func,
                   apply_node_func=None,
                   etype=None):
        """Propagate messages using graph traversal by sequentially triggering
        :func:`pull()` on nodes.

        The traversal order is specified by the ``nodes_generator``. It generates
        node frontiers, which is a list or a tensor of nodes. The nodes in the
        same frontier will be triggered together, while nodes in different frontiers
        will be triggered according to the generating order.

        Parameters
        ----------
        nodes_generator : iterable, each element is a list or a tensor of node ids
            The generator of node frontiers. It specifies which nodes perform
            :func:`pull` at each timestep.
        message_func : callable
            Message function on the edges. The function should be
            an :mod:`Edge UDF <dgl.udf>`.
        reduce_func : callable
            Reduce function on the node. The function should be
            a :mod:`Node UDF <dgl.udf>`.
        apply_node_func : callable, optional
            Apply function on the nodes. The function should be
            a :mod:`Node UDF <dgl.udf>`. (Default: None)
        etype : str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph. (Default: None)

        Examples
        --------
        >>> import torch
        >>> import dgl
        >>> import dgl.function as fn

        Instantiate a heterogrph and perform multiple rounds of message passing.

        >>> g = dgl.graph(([0, 1, 2, 3], [2, 3, 4, 4]), 'user', 'follows')
        >>> g.nodes['user'].data['h'] = torch.tensor([[1.], [2.], [3.], [4.], [5.]])
        >>> g['follows'].prop_nodes([[2, 3], [4]], fn.copy_src('h', 'm'),
        >>>                         fn.sum('m', 'h'), etype='follows')
        tensor([[1.],
                [2.],
                [1.],
                [2.],
                [3.]])

        See Also
        --------
        prop_edges
        """
        for node_frontier in nodes_generator:
            self.pull(node_frontier, message_func, reduce_func, apply_node_func, etype=etype)

    def prop_edges(self,
                   edges_generator,
                   message_func,
                   reduce_func,
                   apply_node_func=None,
                   etype=None):
        """Propagate messages using graph traversal by sequentially triggering
        :func:`send_and_recv()` on edges.

        The traversal order is specified by the ``edges_generator``. It generates
        edge frontiers. The edge frontiers should be of *valid edges type*.
        See :func:`send` for more details.

        Edges in the same frontier will be triggered together, and edges in
        different frontiers will be triggered according to the generating order.

        Parameters
        ----------
        edges_generator : generator
            The generator of edge frontiers.
        message_func : callable
            Message function on the edges. The function should be
            an :mod:`Edge UDF <dgl.udf>`.
        reduce_func : callable
            Reduce function on the node. The function should be
            a :mod:`Node UDF <dgl.udf>`.
        apply_node_func : callable, optional
            Apply function on the nodes. The function should be
            a :mod:`Node UDF <dgl.udf>`. (Default: None)
        etype : str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph. (Default: None)

        Examples
        --------
        >>> import torch
        >>> import dgl
        >>> import dgl.function as fn

        Instantiate a heterogrph and perform multiple rounds of message passing.

        >>> g = dgl.graph(([0, 1, 2, 3], [2, 3, 4, 4]), 'user', 'follows')
        >>> g.nodes['user'].data['h'] = torch.tensor([[1.], [2.], [3.], [4.], [5.]])
        >>> g['follows'].prop_edges([[0, 1], [2, 3]], fn.copy_src('h', 'm'),
        >>>                         fn.sum('m', 'h'), etype='follows')
        >>> g.nodes['user'].data['h']
        tensor([[1.],
                [2.],
                [1.],
                [2.],
                [3.]])

        See Also
        --------
        prop_nodes
        """
        for edge_frontier in edges_generator:
            self.send_and_recv(edge_frontier, message_func, reduce_func,
                               apply_node_func, etype=etype)

    #################################################################
    # Misc
    #################################################################

    def to_networkx(self, node_attrs=None, edge_attrs=None):
        """Convert this graph to networkx graph.

        The edge id will be saved as the 'id' edge attribute.

        Parameters
        ----------
        node_attrs : iterable of str, optional
            The node attributes to be copied.
        edge_attrs : iterable of str, optional
            The edge attributes to be copied.

        Returns
        -------
        networkx.DiGraph
            The nx graph

        Examples
        --------

        .. note:: Here we use pytorch syntax for demo. The general idea applies
            to other frameworks with minor syntax change (e.g. replace
            ``torch.tensor`` with ``mxnet.ndarray``).

        >>> import torch as th
        >>> g = DGLGraph()
        >>> g.add_nodes(5, {'n1': th.randn(5, 10)})
        >>> g.add_edges([0,1,3,4], [2,4,0,3], {'e1': th.randn(4, 6)})
        >>> nxg = g.to_networkx(node_attrs=['n1'], edge_attrs=['e1'])

        See Also
        --------
        dgl.to_networkx
        """
        # TODO(minjie): multi-type support
        assert len(self.ntypes) == 1
        assert len(self.etypes) == 1
        src, dst = self.edges()
        src = F.asnumpy(src)
        dst = F.asnumpy(dst)
        nx_graph = nx.MultiDiGraph() if self.is_multigraph else nx.DiGraph()
        nx_graph.add_nodes_from(range(self.number_of_nodes()))
        for eid, (u, v) in enumerate(zip(src, dst)):
            nx_graph.add_edge(u, v, id=eid)

        if node_attrs is not None:
            for nid, attr in nx_graph.nodes(data=True):
                feat_dict = self._get_n_repr(0, nid)
                attr.update({key: F.squeeze(feat_dict[key], 0) for key in node_attrs})
        if edge_attrs is not None:
            for _, _, attr in nx_graph.edges(data=True):
                eid = attr['id']
                feat_dict = self._get_e_repr(0, eid)
                attr.update({key: F.squeeze(feat_dict[key], 0) for key in edge_attrs})
        return nx_graph

    def filter_nodes(self, predicate, nodes=ALL, ntype=None):
        """Return a tensor of node IDs with the given node type that satisfy
        the given predicate.

        Parameters
        ----------
        predicate : callable
            A function of signature ``func(nodes) -> tensor``.
            ``nodes`` are :class:`NodeBatch` objects as in :mod:`~dgl.udf`.
            The ``tensor`` returned should be a 1-D boolean tensor with
            each element indicating whether the corresponding node in
            the batch satisfies the predicate.
        nodes : int, iterable or tensor of ints
            The nodes to filter on. Default value is all the nodes.
        ntype : str, optional
            The node type. Can be omitted if there is only one node type
            in the graph. (Default: None)

        Returns
        -------
        tensor
            Node ids indicating the nodes that satisfy the predicate.

        Examples
        --------
        >>> import torch
        >>> import dgl
        >>> import dgl.function as fn
        >>> g = dgl.graph([], 'user', 'follows', card=4)
        >>> g.nodes['user'].data['h'] = torch.tensor([[0.], [1.], [1.], [0.]])
        >>> g.filter_nodes(lambda nodes: (nodes.data['h'] == 1.).squeeze(1), ntype='user')
        tensor([1, 2])
        """
        ntid = self.get_ntype_id(ntype)
        if is_all(nodes):
            v = utils.toindex(slice(0, self._graph.number_of_nodes(ntid)))
        else:
            v = utils.toindex(nodes)

        n_repr = self._get_n_repr(ntid, v)
        nbatch = NodeBatch(v, n_repr)
        n_mask = F.copy_to(predicate(nbatch), F.cpu())

        if is_all(nodes):
            return F.nonzero_1d(n_mask)
        else:
            nodes = F.tensor(nodes)
            return F.boolean_mask(nodes, n_mask)

    def filter_edges(self, predicate, edges=ALL, etype=None):
        """Return a tensor of edge IDs with the given edge type that satisfy
        the given predicate.

        Parameters
        ----------
        predicate : callable
            A function of signature ``func(edges) -> tensor``.
            ``edges`` are :class:`EdgeBatch` objects as in :mod:`~dgl.udf`.
            The ``tensor`` returned should be a 1-D boolean tensor with
            each element indicating whether the corresponding edge in
            the batch satisfies the predicate.
        edges : valid edges type
            Edges on which to apply ``func``. See :func:`send` for valid
            edges type. Default value is all the edges.
        etype : str, optional
            The edge type. Can be omitted if there is only one edge type
            in the graph. (Default: None)

        Returns
        -------
        tensor
            Edge ids indicating the edges that satisfy the predicate.

        Examples
        --------
        >>> import torch
        >>> import dgl
        >>> import dgl.function as fn
        >>> g = dgl.graph([(0, 0), (0, 1), (1, 2), (2, 3)], 'user', 'follows')
        >>> g.edges['follows'].data['h'] = torch.tensor([[0.], [1.], [1.], [0.]])
        >>> g.filter_edges(lambda edges: (edges.data['h'] == 1.).squeeze(1), etype='follows')
        tensor([1, 2])
        """
        etid = self.get_etype_id(etype)
        stid, dtid = self._graph.metagraph.find_edge(etid)
        if is_all(edges):
            u, v, _ = self._graph.edges(etid, 'eid')
            eid = utils.toindex(slice(0, self._graph.number_of_edges(etid)))
        elif isinstance(edges, tuple):
            u, v = edges
            u = utils.toindex(u)
            v = utils.toindex(v)
            # Rewrite u, v to handle edge broadcasting and multigraph.
            u, v, eid = self._graph.edge_ids(etid, u, v)
        else:
            eid = utils.toindex(edges)
            u, v, _ = self._graph.find_edges(etid, eid)

        src_data = self._get_n_repr(stid, u)
        edge_data = self._get_e_repr(etid, eid)
        dst_data = self._get_n_repr(dtid, v)
        ebatch = EdgeBatch((u, v, eid), src_data, edge_data, dst_data)
        e_mask = F.copy_to(predicate(ebatch), F.cpu())

        if is_all(edges):
            return F.nonzero_1d(e_mask)
        else:
            edges = F.tensor(edges)
            return F.boolean_mask(edges, e_mask)

    def to(self, ctx):  # pylint: disable=invalid-name
        """Move both ndata and edata to the targeted mode (cpu/gpu)
        Framework agnostic

        Parameters
        ----------
        ctx : framework-specific context object
            The context to move data to.

        Returns
        -------
        g : DGLHeteroGraph
          Moved DGLHeteroGraph of the targeted mode.

        Examples
        --------
        The following example uses PyTorch backend.

        >>> import torch
        >>> g = dgl.bipartite([(0, 0), (1, 0), (1, 2), (2, 1)], 'user', 'plays', 'game')
        >>> g.nodes['user'].data['h'] = torch.tensor([[0.], [1.], [2.]])
        >>> g.edges['plays'].data['h'] = torch.tensor([[0.], [1.], [2.], [3.]])
        >>> g = g.to(torch.device('cuda:0'))
        """
        for i in range(len(self._node_frames)):
            for k in self._node_frames[i].keys():
                self._node_frames[i][k] = F.copy_to(self._node_frames[i][k], ctx)
        for i in range(len(self._edge_frames)):
            for k in self._edge_frames[i].keys():
                self._edge_frames[i][k] = F.copy_to(self._edge_frames[i][k], ctx)
        return self

    def local_var(self):
        """Return a heterograph object that can be used in a local function scope.

        The returned graph object shares the feature data and graph structure of this graph.
        However, any out-place mutation to the feature data will not reflect to this graph,
        thus making it easier to use in a function scope.

        If set, the local graph object will use same initializers for node features and
        edge features.

        Returns
        -------
        DGLHeteroGraph
            The graph object that can be used as a local variable.

        Notes
        -----
        Internally, the returned graph shares the same feature tensors, but construct a new
        dictionary structure (aka. Frame) so adding/removing feature tensors from the returned
        graph will not reflect to the original graph. However, inplace operations do change
        the shared tensor values, so will be reflected to the original graph. This function
        also has little overhead when the number of feature tensors in this graph is small.

        Examples
        --------
        The following example uses PyTorch backend.

        Avoid accidentally overriding existing feature data. This is quite common when
        implementing a NN module:

        >>> def foo(g):
        >>>     g = g.local_var()
        >>>     g.edata['h'] = torch.ones((g.number_of_edges(), 3))
        >>>     return g.edata['h']
        >>>
        >>> g = dgl.bipartite([(0, 0), (1, 0), (1, 2)], 'user', 'plays', 'game')
        >>> g.edata['h'] = torch.zeros((g.number_of_edges(), 3))
        >>> newh = foo(g)        # get tensor of all ones
        >>> print(g.edata['h'])  # still get tensor of all zeros

        Automatically garbage collect locally-defined tensors without the need to manually
        ``pop`` the tensors.

        >>> def foo(g):
        >>>     g = g.local_var()
        >>>     # This 'h' feature will stay local and be GCed when the function exits
        >>>     g.edata['h'] = torch.ones((g.number_of_edges(), 3))
        >>>     return g.edata['h']
        >>>
        >>> g = dgl.bipartite([(0, 0), (1, 0), (1, 2)], 'user', 'plays', 'game')
        >>> h = foo(g)
        >>> print('h' in g.edata)
        False

        See Also
        --------
        local_var
        """
        local_node_frames = [FrameRef(Frame(fr._frame)) for fr in self._node_frames]
        local_edge_frames = [FrameRef(Frame(fr._frame)) for fr in self._edge_frames]
        # Use same per-column initializers and default initializer.
        # If registered, a column (based on key) initializer will be used first,
        # otherwise the default initializer will be used.
        for fr1, fr2 in zip(local_node_frames, self._node_frames):
            sync_frame_initializer(fr1._frame, fr2._frame)
        for fr1, fr2 in zip(local_edge_frames, self._edge_frames):
            sync_frame_initializer(fr1._frame, fr2._frame)
        return DGLHeteroGraph(self._graph, self.ntypes, self.etypes,
                              local_node_frames,
                              local_edge_frames)

    @contextmanager
    def local_scope(self):
        """Enter a local scope context for this graph.

        By entering a local scope, any out-place mutation to the feature data will
        not reflect to the original graph, thus making it easier to use in a function scope.

        If set, the local scope will use same initializers for node features and
        edge features.

        Examples
        --------
        The following example uses PyTorch backend.

        Avoid accidentally overriding existing feature data. This is quite common when
        implementing a NN module:

        >>> def foo(g):
        >>>     with g.local_scope():
        >>>         g.edata['h'] = torch.ones((g.number_of_edges(), 3))
        >>>         return g.edata['h']
        >>>
        >>> g = dgl.bipartite([(0, 0), (1, 0), (1, 2)], 'user', 'plays', 'game')
        >>> g.edata['h'] = torch.zeros((g.number_of_edges(), 3))
        >>> newh = foo(g)        # get tensor of all ones
        >>> print(g.edata['h'])  # still get tensor of all zeros

        Automatically garbage collect locally-defined tensors without the need to manually
        ``pop`` the tensors.

        >>> def foo(g):
        >>>     with g.local_scope():
        >>>         # This 'h' feature will stay local and be GCed when the function exits
        >>>         g.edata['h'] = torch.ones((g.number_of_edges(), 3))
        >>>         return g.edata['h']
        >>>
        >>> g = dgl.bipartite([(0, 0), (1, 0), (1, 2)], 'user', 'plays', 'game')
        >>> h = foo(g)
        >>> print('h' in g.edata)
        False

        See Also
        --------
        local_var
        """
        old_nframes = self._node_frames
        old_eframes = self._edge_frames
        self._node_frames = [FrameRef(Frame(fr._frame)) for fr in self._node_frames]
        self._edge_frames = [FrameRef(Frame(fr._frame)) for fr in self._edge_frames]
        # Use same per-column initializers and default initializer.
        # If registered, a column (based on key) initializer will be used first,
        # otherwise the default initializer will be used.
        for fr1, fr2 in zip(self._node_frames, old_nframes):
            sync_frame_initializer(fr1._frame, fr2._frame)
        for fr1, fr2 in zip(self._edge_frames, old_eframes):
            sync_frame_initializer(fr1._frame, fr2._frame)
        yield
        self._node_frames = old_nframes
        self._edge_frames = old_eframes

############################################################
# Internal APIs
############################################################

def make_canonical_etypes(etypes, ntypes, metagraph):
    """Internal function to convert etype name to (srctype, etype, dsttype)

    Parameters
    ----------
    etypes : list of str
        Edge type list
    ntypes : list of str
        Node type list
    metagraph : GraphIndex
        Meta graph.

    Returns
    -------
    list of tuples (srctype, etype, dsttype)
    """
    # sanity check
    if len(etypes) != metagraph.number_of_edges():
        raise DGLError('Length of edge type list must match the number of '
                       'edges in the metagraph. {} vs {}'.format(
                           len(etypes), metagraph.number_of_edges()))
    if len(ntypes) != metagraph.number_of_nodes():
        raise DGLError('Length of nodes type list must match the number of '
                       'nodes in the metagraph. {} vs {}'.format(
                           len(ntypes), metagraph.number_of_nodes()))
    src, dst, eid = metagraph.edges()
    rst = [(ntypes[sid], etypes[eid], ntypes[did]) for sid, did, eid in zip(src, dst, eid)]
    return rst

def infer_ntype_from_dict(graph, etype_dict):
    """Infer node type from dictionary of edge type to values.

    All the edge types in the dict must share the same destination node type
    and the node type will be returned. Otherwise, throw error.

    Parameters
    ----------
    graph : DGLHeteroGraph
        Graph
    etype_dict : dict
        Dictionary whose key is edge type

    Returns
    -------
    str
        Node type
    """
    ntype = None
    for ety in etype_dict:
        _, _, dty = graph.to_canonical_etype(ety)
        if ntype is None:
            ntype = dty
        if ntype != dty:
            raise DGLError("Cannot infer destination node type from the dictionary. "
                           "A valid specification must make sure that all the edge "
                           "type keys share the same destination node type.")
    return ntype

def pad_tuple(tup, length, pad_val=None):
    """Pad the given tuple to the given length.

    If the input is not a tuple, convert it to a tuple of length one.
    Return None if pad fails.
    """
    if not isinstance(tup, tuple):
        tup = (tup, )
    if len(tup) > length:
        return None
    elif len(tup) == length:
        return tup
    else:
        return tup + (pad_val,) * (length - len(tup))

def merge_frames(frames, reducer):
    """Merge input frames into one. Resolve conflict fields using reducer.

    Parameters
    ----------
    frames : list of FrameRef
        Input frames
    reducer : str
        One of "sum", "max", "min", "mean", "stack"

    Returns
    -------
    FrameRef
        Merged frame
    """
    if len(frames) == 1:
        return frames[0]
    if reducer == 'stack':
        # TODO(minjie): Stack order does not matter. However, it must
        #   be consistent! Need to enforce one type of order.
        def merger(flist):
            flist = [F.unsqueeze(f, 1) for f in flist]
            return F.stack(flist, 1)
    else:
        redfn = getattr(F, reducer, None)
        if redfn is None:
            raise DGLError('Invalid cross type reducer. Must be one of '
                           '"sum", "max", "min", "mean" or "stack".')
        def merger(flist):
            return redfn(F.stack(flist, 0), 0)
    ret = FrameRef(frame_like(frames[0]._frame))
    keys = set()
    for frm in frames:
        keys.update(frm.keys())
    for k in keys:
        flist = []
        for frm in frames:
            if k in frm:
                flist.append(frm[k])
        if len(flist) > 1:
            ret[k] = merger(flist)
        else:
            ret[k] = flist[0]
    return ret

def combine_frames(frames, ids):
    """Merge the frames into one frame, taking the common columns.

    Return None if there is no common columns.

    Parameters
    ----------
    frames : List[FrameRef]
        List of frames
    ids : List[int]
        List of frame IDs

    Returns
    -------
    FrameRef
        The resulting frame
    """
    # find common columns and check if their schemes match
    schemes = {key: scheme for key, scheme in frames[ids[0]].schemes.items()}
    for frame_id in ids:
        frame = frames[frame_id]
        for key, scheme in list(schemes.items()):
            if key in frame.schemes:
                if frame.schemes[key] != scheme:
                    raise DGLError('Cannot concatenate column %s with shape %s and shape %s' %
                                   (key, frame.schemes[key], scheme))
            else:
                del schemes[key]

    if len(schemes) == 0:
        return None

    # concatenate the columns
    to_cat = lambda key: [frames[i][key] for i in ids if frames[i].num_rows > 0]
    cols = {key: F.cat(to_cat(key), dim=0) for key in schemes}
    return FrameRef(Frame(cols))

def combine_names(names, ids=None):
    """Combine the selected names into one new name.

    Parameters
    ----------
    names : list of str
        String names
    ids : numpy.ndarray, optional
        Selected index

    Returns
    -------
    str
    """
    if ids is None:
        return '+'.join(sorted(names))
    else:
        selected = sorted([names[i] for i in ids])
        return '+'.join(selected)

class AdaptedHeteroGraph(GraphAdapter):
    """Adapt DGLGraph to interface required by scheduler.

    Parameters
    ----------
    graph : DGLHeteroGraph
        Graph
    stid : int
        Source node type id
    dtid : int
        Destination node type id
    etid : int
        Edge type id
    """
    def __init__(self, graph, stid, dtid, etid):
        self.graph = graph
        self.stid = stid
        self.dtid = dtid
        self.etid = etid

    @property
    def gidx(self):
        return self.graph._graph

    def num_src(self):
        """Number of source nodes."""
        return self.graph._graph.number_of_nodes(self.stid)

    def num_dst(self):
        """Number of destination nodes."""
        return self.graph._graph.number_of_nodes(self.dtid)

    def num_edges(self):
        """Number of edges."""
        return self.graph._graph.number_of_edges(self.etid)

    @property
    def srcframe(self):
        """Frame to store source node features."""
        return self.graph._node_frames[self.stid]

    @property
    def dstframe(self):
        """Frame to store source node features."""
        return self.graph._node_frames[self.dtid]

    @property
    def edgeframe(self):
        """Frame to store edge features."""
        return self.graph._edge_frames[self.etid]

    @property
    def msgframe(self):
        """Frame to store messages."""
        return self.graph._msg_frames[self.etid]

    @property
    def msgindicator(self):
        """Message indicator tensor."""
        return self.graph._get_msg_index(self.etid)

    @msgindicator.setter
    def msgindicator(self, val):
        """Set new message indicator tensor."""
        self.graph._set_msg_index(self.etid, val)

    def in_edges(self, nodes):
        return self.graph._graph.in_edges(self.etid, nodes)

    def out_edges(self, nodes):
        return self.graph._graph.out_edges(self.etid, nodes)

    def edges(self, form):
        return self.graph._graph.edges(self.etid, form)

    def get_immutable_gidx(self, ctx):
        return self.graph._graph.get_unitgraph(self.etid, ctx)

    def bits_needed(self):
        return self.graph._graph.bits_needed(self.etid)
