from __future__ import absolute_import

from distutils.version import LooseVersion

import torch as th
import builtins
from torch.utils import dlpack

from ... import ndarray as nd
from ... import kernel as K
from ...function.base import TargetCode

TH_VERSION = LooseVersion(th.__version__)

def data_type_dict():
    return {'float16' : th.float16,
            'float32' : th.float32,
            'float64' : th.float64,
            'uint8'   : th.uint8,
            'int8'    : th.int8,
            'int16'   : th.int16,
            'int32'   : th.int32,
            'int64'   : th.int64}

def cpu():
    return th.device('cpu')

def tensor(data, dtype=None):
    return th.tensor(data, dtype=dtype)

def as_scalar(data):
    return data.item()

def get_preferred_sparse_format():
    """Get the preferred sparse matrix format supported by the backend.

    Different backends have their preferred backend. This info is useful when
    constructing a sparse matrix.
    """
    return "coo"

def sparse_matrix(data, index, shape, force_format=False):
    fmt = index[0]
    if fmt != 'coo':
        raise TypeError('Pytorch backend only supports COO format. But got %s.' % fmt)
    spmat = th.sparse_coo_tensor(index[1], data, shape)
    return spmat, None

def sparse_matrix_indices(spmat):
    return ('coo', spmat._indices())

def is_tensor(obj):
    return isinstance(obj, th.Tensor)

def shape(input):
    return input.shape

def dtype(input):
    return input.dtype

def ndim(input):
    return input.dim()

def context(input):
    return input.device

def device_type(ctx):
    return ctx.type

def device_id(ctx):
    if ctx.index is None:
        return 0
    else:
        return ctx.index

def astype(input, ty):
    return input.type(ty)

def asnumpy(input):
    if isinstance(input, th.sparse.FloatTensor):
        return input.to_dense().cpu().detach().numpy()
    else:
        return input.cpu().detach().numpy()

def copy_to(input, ctx):
    if ctx.type == 'cpu':
        return input.cpu()
    elif ctx.type == 'cuda':
        if ctx.index is not None:
            th.cuda.set_device(ctx.index)
        return input.cuda()
    else:
        raise RuntimeError('Invalid context', ctx)

def sum(input, dim, keepdims=False):
    return th.sum(input, dim=dim, keepdim=keepdims)

def reduce_sum(input):
    return input.sum()

def mean(input, dim):
    return th.mean(input, dim=dim)

def reduce_mean(input):
    return input.mean()

def max(input, dim):
    # NOTE: the second argmax array is not returned
    return th.max(input, dim=dim)[0]

def reduce_max(input):
    return input.max()

def min(input, dim):
    # NOTE: the second argmin array is not returned
    return th.min(input, dim=dim)[0]

def reduce_min(input):
    return input.min()

def argsort(input, dim, descending):
    return th.argsort(input, dim=dim, descending=descending)

def topk(input, k, dim, descending=True):
    return th.topk(input, k, dim, largest=descending)[0]

def argtopk(input, k, dim, descending=True):
    return th.topk(input, k, dim, largest=descending)[1]

def exp(input):
    return th.exp(input)

def softmax(input, dim=-1):
    return th.softmax(input, dim=dim)

def cat(seq, dim):
    return th.cat(seq, dim=dim)

def stack(seq, dim):
    return th.stack(seq, dim=dim)

def split(input, sizes_or_sections, dim):
    return th.split(input, sizes_or_sections, dim)

def repeat(input, repeats, dim):
    # return th.repeat_interleave(input, repeats, dim) # PyTorch 1.1
    if dim < 0:
        dim += input.dim()
    return th.flatten(th.stack([input] * repeats, dim=dim+1), dim, dim+1)

def gather_row(data, row_index):
    return th.index_select(data, 0, row_index)

def slice_axis(data, axis, begin, end):
    return th.narrow(data, axis, begin, end - begin)

def take(data, indices, dim):
    new_shape = data.shape[:dim] + indices.shape + data.shape[dim+1:]
    return th.index_select(data, dim, indices.view(-1)).view(new_shape)

def narrow_row(x, start, stop):
    return x[start:stop]

def scatter_row(data, row_index, value):
    return data.index_copy(0, row_index, value)

def scatter_row_inplace(data, row_index, value):
    data[row_index] = value

def squeeze(input, dim):
    return th.squeeze(input, dim)

def unsqueeze(input, dim):
    return th.unsqueeze(input, dim)

def reshape(input, shape):
    return th.reshape(input ,shape)

def swapaxes(input, axis1, axis2):
    return th.transpose(input, axis1, axis2)

def zeros(shape, dtype, ctx):
    return th.zeros(shape, dtype=dtype, device=ctx)

def zeros_like(input):
    return th.zeros_like(input)

def ones(shape, dtype, ctx):
    return th.ones(shape, dtype=dtype, device=ctx)

def uniform(shape, dtype, ctx, low, high):
    return th.empty(shape, dtype=dtype, device=ctx).uniform_(low, high)

def pad_packed_tensor(input, lengths, value, l_min=None):
    old_shape = input.shape
    if isinstance(lengths, th.Tensor):
        max_len = as_scalar(lengths.max())
    else:
        max_len = builtins.max(lengths)

    if l_min is not None:
        max_len = builtins.max(max_len, l_min)

    batch_size = len(lengths)
    device = input.device
    x = input.new(batch_size * max_len, *old_shape[1:])
    x.fill_(value)
    index = []
    for i, l in enumerate(lengths):
        index.extend(range(i * max_len, i * max_len + l))
    index = th.tensor(index).to(device)
    return scatter_row(x, index, input).view(batch_size, max_len, *old_shape[1:])

def pack_padded_tensor(input, lengths):
    batch_size, max_len = input.shape[:2]
    device = input.device
    index = []
    for i, l in enumerate(lengths):
        index.extend(range(i * max_len, i * max_len + l))
    index = th.tensor(index).to(device)
    return gather_row(input.view(batch_size * max_len, -1), index)

def unsorted_1d_segment_sum(input, seg_id, n_segs, dim):
    y = th.zeros(n_segs, *input.shape[1:]).to(input)
    seg_id = seg_id.view((-1,) + (1,) * (input.dim() - 1)).expand_as(input)
    y = y.scatter_add_(dim, seg_id, input)
    return y

def unsorted_1d_segment_mean(input, seg_id, n_segs, dim):
    w = unsorted_1d_segment_sum(th.ones_like(seg_id), seg_id, n_segs, 0).to(input)
    w = w.clamp(min=1)   # remove 0 entries
    y = unsorted_1d_segment_sum(input, seg_id, n_segs, dim)
    y = y / w.view((-1,) + (1,) * (y.dim() - 1))
    return y

def boolean_mask(input, mask):
    return input[mask]

def equal(x, y):
    return x == y

def logical_not(input):
    return ~input

def unique(input):
    return th.unique(input)

def full_1d(length, fill_value, dtype, ctx):
    return th.full((length,), fill_value, dtype=dtype, device=ctx)

def nonzero_1d(input):
    x = th.nonzero(input).squeeze()
    return x if x.dim() == 1 else x.view(-1)

def sort_1d(input):
    return th.sort(input)

def arange(start, stop):
    return th.arange(start, stop, dtype=th.int64)

def rand_shuffle(arr):
    idx = th.randperm(len(arr))
    return arr[idx]

def zerocopy_to_dlpack(input):
    return dlpack.to_dlpack(input.contiguous())

def zerocopy_from_dlpack(dlpack_tensor):
    return dlpack.from_dlpack(dlpack_tensor)

def zerocopy_to_numpy(input):
    # NOTE: not zerocopy
    return asnumpy(input)

def zerocopy_from_numpy(np_array):
    return th.as_tensor(np_array)

def zerocopy_to_dgl_ndarray(input):
    return nd.from_dlpack(dlpack.to_dlpack(input.contiguous()))

def zerocopy_from_dgl_ndarray(input):
    return dlpack.from_dlpack(input.to_dlpack())



class BinaryReduce(th.autograd.Function):
    @staticmethod
    def forward(ctx, reducer, binary_op, graph, lhs, rhs, lhs_data, rhs_data,
                out_size, lhs_map, rhs_map, out_map):
        lhs_data_nd = zerocopy_to_dgl_ndarray(lhs_data)
        rhs_data_nd = zerocopy_to_dgl_ndarray(rhs_data)
        feat_shape = K.infer_binary_feature_shape(binary_op, lhs_data_nd, rhs_data_nd)
        out_shape = feat_shape
        if binary_op == 'dot':
            out_shape = feat_shape[:-1]
        out_data = lhs_data.new_empty((out_size,) + out_shape)
        out_data_nd = zerocopy_to_dgl_ndarray(out_data)
        K.binary_op_reduce(
            reducer if reducer != 'mean' else 'sum',
            binary_op, graph, lhs, rhs, lhs_data_nd, rhs_data_nd,
            out_data_nd, lhs_map[0], rhs_map[0], out_map[0])
        # normalize if mean reducer
        # NOTE(zihao): this is a temporary hack and we should have better solution in the future.
        if reducer == 'mean':
            degs = lhs_data.new_empty((out_data.shape[0],))
            degs_nd = zerocopy_to_dgl_ndarray(degs)
            if lhs != TargetCode.DST: # src or edge
                target = lhs
                n = lhs_data.shape[0]
                in_map = lhs_map[0]
            else: # rhs != TargetCode.DST
                target = rhs
                n = rhs_data.shape[0]
                in_map = rhs_map[0]
            in_ones = lhs_data.new_ones((n,))
            in_ones_nd = zerocopy_to_dgl_ndarray(in_ones)
            K.copy_reduce(
                'sum', graph, target, in_ones_nd, degs_nd, in_map, out_map[0])
            # reshape
            degs = degs.reshape((out_data.shape[0],) + (1,) * (out_data.dim() - 1)).clamp(min=1)
            out_data = out_data / degs
        else:
            degs = None
        # save_for_backward can only save variables
        ctx.backward_cache = (reducer, binary_op, graph, lhs, rhs, lhs_map,
                              rhs_map, out_map, lhs_data_nd, rhs_data_nd,
                              feat_shape, degs)
        ctx.save_for_backward(out_data)
        return out_data

    @staticmethod
    def backward(ctx, grad_out):
        reducer, binary_op, graph, lhs, rhs, lhs_map, rhs_map, out_map, \
            lhs_data_nd, rhs_data_nd, feat_shape, degs \
            = ctx.backward_cache
        out_data, = ctx.saved_tensors
        out_data_nd = zerocopy_to_dgl_ndarray(out_data)
        grad_lhs = None
        grad_rhs = None
        if reducer == 'mean':
            grad_out = grad_out / degs
        grad_out_nd = zerocopy_to_dgl_ndarray(grad_out)
        if ctx.needs_input_grad[5]:
            grad_lhs = grad_out.new_empty((lhs_data_nd.shape[0],) + feat_shape)
            K.backward_lhs_binary_op_reduce(
                reducer if reducer != 'mean' else 'sum',
                binary_op, graph, lhs, rhs, lhs_data_nd, rhs_data_nd,
                out_data_nd, grad_out_nd, zerocopy_to_dgl_ndarray(grad_lhs),
                lhs_map[1], rhs_map[1], out_map[1])
            grad_lhs = _reduce_grad(grad_lhs, lhs_data_nd.shape)
        if ctx.needs_input_grad[6]:
            grad_rhs = grad_out.new_empty((rhs_data_nd.shape[0],) + feat_shape)
            K.backward_rhs_binary_op_reduce(
                reducer if reducer != 'mean' else 'sum',
                binary_op, graph, lhs, rhs, lhs_data_nd, rhs_data_nd,
                out_data_nd, grad_out_nd, zerocopy_to_dgl_ndarray(grad_rhs),
                lhs_map[1], rhs_map[1], out_map[1])
            grad_rhs = _reduce_grad(grad_rhs, rhs_data_nd.shape)

        return None, None, None, None, None, grad_lhs, grad_rhs, None, None, \
            None, None


class CopyReduce(th.autograd.Function):
    @staticmethod
    def forward(ctx, reducer, graph, target, in_data, out_size, in_map,
                out_map):
        out_data = in_data.new_empty((out_size,) + in_data.shape[1:])
        in_data_nd = zerocopy_to_dgl_ndarray(in_data)
        out_data_nd = zerocopy_to_dgl_ndarray(out_data)
        K.copy_reduce(
            reducer if reducer != 'mean' else 'sum', 
            graph, target, in_data_nd, out_data_nd, in_map[0], out_map[0])
        # normalize if mean reducer
        # NOTE(zihao): this is a temporary hack and we should have better solution in the future.
        if reducer == 'mean':
            in_ones = in_data.new_ones((in_data.shape[0],))
            degs = in_data.new_empty((out_data.shape[0],))
            in_ones_nd = zerocopy_to_dgl_ndarray(in_ones)
            degs_nd = zerocopy_to_dgl_ndarray(degs)
            K.copy_reduce(
                'sum', graph, target, in_ones_nd, degs_nd, in_map[0], out_map[0]) 
            # reshape
            degs = degs.reshape((out_data.shape[0],) + (1,) * (out_data.dim() - 1)).clamp(min=1)
            out_data = out_data / degs
        else:
            degs = None
        # save_for_backward can only save variables
        ctx.backward_cache = (reducer, graph, target, in_map, out_map,
                              in_data_nd, degs)
        ctx.save_for_backward(out_data)
        return out_data

    @staticmethod
    def backward(ctx, grad_out):
        reducer, graph, target, in_map, out_map, in_data_nd, degs \
            = ctx.backward_cache
        out_data, = ctx.saved_tensors
        out_data_nd = zerocopy_to_dgl_ndarray(out_data)
        grad_in = None
        if reducer == 'mean':
            grad_out = grad_out / degs
        grad_out_nd = zerocopy_to_dgl_ndarray(grad_out)
        if ctx.needs_input_grad[3]:
            grad_in = grad_out.new_empty(in_data_nd.shape)
            K.backward_copy_reduce(
                reducer if reducer != 'mean' else 'sum', 
                graph, target, in_data_nd, out_data_nd, grad_out_nd, 
                zerocopy_to_dgl_ndarray(grad_in), in_map[1], out_map[1])
        return None, None, None, grad_in, None, None, None


binary_reduce = BinaryReduce.apply
copy_reduce = CopyReduce.apply


def _reduce_grad(grad, shape):
    """Reduce gradient on the broadcast dimension

    If there is broadcast in forward pass, gradients need to be reduced on
    broadcast dimension. This function checks the input tensor shape and
    gradient shape and perform the reduction.

    Parameters
    ----------
    grad: Tensor
        Gradient tensor
    shape: tuple
        Shape of input tensor

    Returns
    -------
    Tensor
    """
    grad_shape = grad.shape[1:]
    in_shape = shape[1:]
    if in_shape == grad_shape:
        # no need to reduce
        return grad
    num_to_squeeze = len(grad_shape) - len(in_shape)
    # pad inshape
    in_shape = (1,) * num_to_squeeze + in_shape
    reduce_idx = th.nonzero(th.tensor(grad_shape) - th.tensor(in_shape))
    reduce_idx += 1  # skip batch dim
    grad = grad.sum(dim=tuple(reduce_idx), keepdim=True)
    return grad.view(shape)

def sync():
    # Pytorch performs computation synchronously, so no need for synchronization.
    pass
