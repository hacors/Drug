/*!
 *  Copyright (c) 2018 by Contributors
 * \file graph/network.cc
 * \brief DGL networking related APIs
 */
#include "./network.h"

#include <dgl/runtime/container.h>
#include <dgl/runtime/ndarray.h>
#include <dgl/packed_func_ext.h>
#include <dgl/immutable_graph.h>
#include <dgl/nodeflow.h>

#include <unordered_map>

#include "./network/communicator.h"
#include "./network/socket_communicator.h"
#include "./network/msg_queue.h"
#include "./network/common.h"

using dgl::network::StringPrintf;
using namespace dgl::runtime;

namespace dgl {
namespace network {

NDArray CreateNDArrayFromRaw(std::vector<int64_t> shape,
                             DLDataType dtype,
                             DLContext ctx,
                             void* raw) {
  DLTensor tensor;
  tensor.ctx = ctx;
  tensor.ndim = static_cast<int>(shape.size());
  tensor.dtype = dtype;
  tensor.shape = new int64_t[tensor.ndim];
  for (int i = 0; i < tensor.ndim; ++i) {
    tensor.shape[i] = shape[i];
  }
  tensor.strides = new int64_t[tensor.ndim];
  for (int i = 0; i < tensor.ndim; ++i) {
    tensor.strides[i] = 1;
  }
  for (int i = tensor.ndim - 2; i >= 0; --i) {
    tensor.strides[i] = tensor.shape[i+1] * tensor.strides[i+1];
  }
  tensor.data = raw;
  DLManagedTensor *managed_tensor = new DLManagedTensor();
  managed_tensor->dl_tensor = tensor;
  return NDArray::FromDLPack(managed_tensor);
}

void ArrayMeta::AddArray(const NDArray& array) {
  // We first write the ndim to the data_shape_
  data_shape_.push_back(static_cast<int64_t>(array->ndim));
  // Then we write the data shape
  for (int i = 0; i < array->ndim; ++i) {
    data_shape_.push_back(array->shape[i]);
  }
  ndarray_count_++;
}

char* ArrayMeta::Serialize(int64_t* size) {
  char* buffer = nullptr;
  int64_t buffer_size = 0;
  buffer_size += sizeof(msg_type_);
  if (ndarray_count_ != 0) {
    buffer_size += sizeof(ndarray_count_);
    buffer_size += sizeof(data_shape_.size());
    buffer_size += sizeof(int64_t) * data_shape_.size();
  }
  // In the future, we should have a better memory management as
  // allocating a large chunk of memory can be very expensive.
  buffer = new char[buffer_size];
  char* pointer = buffer;
  // Write msg_type_
  *(reinterpret_cast<int*>(pointer)) = msg_type_;
  pointer += sizeof(msg_type_);
  if (ndarray_count_ != 0) {
    // Write ndarray_count_
    *(reinterpret_cast<int*>(pointer)) = ndarray_count_;
    pointer += sizeof(ndarray_count_);
    // Write size of data_shape_
    *(reinterpret_cast<size_t*>(pointer)) = data_shape_.size();
    pointer += sizeof(data_shape_.size());
    // Write data of data_shape_
    memcpy(pointer,
        reinterpret_cast<char*>(data_shape_.data()),
        sizeof(int64_t) * data_shape_.size());
  }
  *size = buffer_size;
  return buffer;
}

void ArrayMeta::Deserialize(char* buffer, int64_t size) {
  int64_t data_size = 0;
  // Read mesg_type_
  msg_type_ = *(reinterpret_cast<int*>(buffer));
  buffer += sizeof(int);
  data_size += sizeof(int);
  if (data_size < size) {
    // Read ndarray_count_
    ndarray_count_ = *(reinterpret_cast<int*>(buffer));
    buffer += sizeof(int);
    data_size += sizeof(int);
    // Read size of data_shape_
    size_t count = *(reinterpret_cast<size_t*>(buffer));
    buffer += sizeof(size_t);
    data_size += sizeof(size_t);
    data_shape_.resize(count);
    // Read data of data_shape_
    memcpy(data_shape_.data(), buffer,
        count * sizeof(int64_t));
    data_size += count * sizeof(int64_t);
  }
  CHECK_EQ(data_size, size);
}

char* KVStoreMsg::Serialize(int64_t* size) {
  char* buffer = nullptr;
  int64_t buffer_size = 0;
  buffer_size += sizeof(this->msg_type);
  buffer_size += sizeof(this->rank);
  if (!this->name.empty()) {
    buffer_size += sizeof(this->name.size());
    buffer_size += this->name.size();
  }
  // In the future, we should have a better memory management as
  // allocating a large chunk of memory can be very expensive.
  buffer = new char[buffer_size];
  char* pointer = buffer;
  // write msg_type
  *(reinterpret_cast<int*>(pointer)) = this->msg_type;
  pointer += sizeof(this->msg_type);
  // write rank
  *(reinterpret_cast<int*>(pointer)) = this->rank;
  pointer += sizeof(this->rank);
  // write name
  if (!this->name.empty()) {
    *(reinterpret_cast<size_t*>(pointer)) = this->name.size();
    pointer += sizeof(size_t);
    memcpy(pointer, this->name.c_str(), this->name.size());
  }
  *size = buffer_size;
  return buffer;
}

void KVStoreMsg::Deserialize(char* buffer, int64_t size) {
  int64_t data_size = 0;
  // Read msg_type
  this->msg_type = *(reinterpret_cast<int*>(buffer));
  buffer += sizeof(int);
  data_size += sizeof(int);
  // Read rank
  this->rank = *(reinterpret_cast<int*>(buffer));
  buffer += sizeof(int);
  data_size += sizeof(int);
  if (data_size < size) {
    // Read name
    size_t name_size = *(reinterpret_cast<size_t*>(buffer));
    buffer += sizeof(name_size);
    data_size += sizeof(name_size);
    this->name.assign(buffer, name_size);
    data_size += name_size;
  }
  CHECK_EQ(data_size, size);
}

////////////////////////////////// Basic Networking Components ////////////////////////////////


DGL_REGISTER_GLOBAL("network._CAPI_DGLSenderCreate")
.set_body([] (DGLArgs args, DGLRetValue* rv) {
    std::string type = args[0];
    int64_t msg_queue_size = args[1];
    network::Sender* sender = nullptr;
    if (type == "socket") {
      sender = new network::SocketSender(msg_queue_size);
    } else {
      LOG(FATAL) << "Unknown communicator type: " << type;
    }
    CommunicatorHandle chandle = static_cast<CommunicatorHandle>(sender);
    *rv = chandle;
  });

DGL_REGISTER_GLOBAL("network._CAPI_DGLReceiverCreate")
.set_body([] (DGLArgs args, DGLRetValue* rv) {
    std::string type = args[0];
    int64_t msg_queue_size = args[1];
    network::Receiver* receiver = nullptr;
    if (type == "socket") {
      receiver = new network::SocketReceiver(msg_queue_size);
    } else {
      LOG(FATAL) << "Unknown communicator type: " << type;
    }
    CommunicatorHandle chandle = static_cast<CommunicatorHandle>(receiver);
    *rv = chandle;
  });

DGL_REGISTER_GLOBAL("network._CAPI_DGLFinalizeSender")
.set_body([] (DGLArgs args, DGLRetValue* rv) {
    CommunicatorHandle chandle = args[0];
    network::Sender* sender = static_cast<network::Sender*>(chandle);
    sender->Finalize();
  });

DGL_REGISTER_GLOBAL("network._CAPI_DGLFinalizeReceiver")
.set_body([] (DGLArgs args, DGLRetValue* rv) {
    CommunicatorHandle chandle = args[0];
    network::Receiver* receiver = static_cast<network::SocketReceiver*>(chandle);
    receiver->Finalize();
  });

DGL_REGISTER_GLOBAL("network._CAPI_DGLSenderAddReceiver")
.set_body([] (DGLArgs args, DGLRetValue* rv) {
    CommunicatorHandle chandle = args[0];
    std::string ip = args[1];
    int port = args[2];
    int recv_id = args[3];
    network::Sender* sender = static_cast<network::Sender*>(chandle);
    std::string addr;
    if (sender->Type() == "socket") {
      addr = StringPrintf("socket://%s:%d", ip.c_str(), port);
    } else {
      LOG(FATAL) << "Unknown communicator type: " << sender->Type();
    }
    sender->AddReceiver(addr.c_str(), recv_id);
  });

DGL_REGISTER_GLOBAL("network._CAPI_DGLSenderConnect")
.set_body([] (DGLArgs args, DGLRetValue* rv) {
    CommunicatorHandle chandle = args[0];
    network::Sender* sender = static_cast<network::Sender*>(chandle);
    if (sender->Connect() == false) {
      LOG(FATAL) << "Sender connection failed.";
    }
  });

DGL_REGISTER_GLOBAL("network._CAPI_DGLReceiverWait")
.set_body([] (DGLArgs args, DGLRetValue* rv) {
    CommunicatorHandle chandle = args[0];
    std::string ip = args[1];
    int port = args[2];
    int num_sender = args[3];
    network::Receiver* receiver = static_cast<network::SocketReceiver*>(chandle);
    std::string addr;
    if (receiver->Type() == "socket") {
      addr = StringPrintf("socket://%s:%d", ip.c_str(), port);
    } else {
      LOG(FATAL) << "Unknown communicator type: " << receiver->Type();
    }
    if (receiver->Wait(addr.c_str(), num_sender) == false) {
      LOG(FATAL) << "Wait sender socket failed.";
    }
  });


////////////////////////// Distributed Sampler Components ////////////////////////////////


DGL_REGISTER_GLOBAL("network._CAPI_SenderSendNodeFlow")
.set_body([] (DGLArgs args, DGLRetValue* rv) {
    CommunicatorHandle chandle = args[0];
    int recv_id = args[1];
    GraphRef g = args[2];
    NDArray node_mapping = args[3];
    NDArray edge_mapping = args[4];
    NDArray layer_offsets = args[5];
    NDArray flow_offsets = args[6];
    auto ptr = std::dynamic_pointer_cast<ImmutableGraph>(g.sptr());
    CHECK(ptr) << "only immutable graph is allowed in send/recv";
    auto csr = ptr->GetInCSR();
    // Create a message for the meta data of ndarray
    NDArray indptr = csr->indptr();
    NDArray indice = csr->indices();
    NDArray edge_ids = csr->edge_ids();
    ArrayMeta meta(kNodeFlowMsg);
    meta.AddArray(node_mapping);
    meta.AddArray(edge_mapping);
    meta.AddArray(layer_offsets);
    meta.AddArray(flow_offsets);
    meta.AddArray(indptr);
    meta.AddArray(indice);
    meta.AddArray(edge_ids);
    // send meta message
    int64_t size = 0;
    char* data = meta.Serialize(&size);
    network::Sender* sender = static_cast<network::Sender*>(chandle);
    Message send_msg;
    send_msg.data = data;
    send_msg.size = size;
    send_msg.deallocator = DefaultMessageDeleter;
    CHECK_EQ(sender->Send(send_msg, recv_id), ADD_SUCCESS);
    // send node_mapping
    Message node_mapping_msg;
    node_mapping_msg.data = static_cast<char*>(node_mapping->data);
    node_mapping_msg.size = node_mapping.GetSize();
    // capture the array in the closure
    node_mapping_msg.deallocator = [node_mapping](Message*) {};
    CHECK_EQ(sender->Send(node_mapping_msg, recv_id), ADD_SUCCESS);
    // send edege_mapping
    Message edge_mapping_msg;
    edge_mapping_msg.data = static_cast<char*>(edge_mapping->data);
    edge_mapping_msg.size = edge_mapping.GetSize();
    // capture the array in the closure
    edge_mapping_msg.deallocator = [edge_mapping](Message*) {};
    CHECK_EQ(sender->Send(edge_mapping_msg, recv_id), ADD_SUCCESS);
    // send layer_offsets
    Message layer_offsets_msg;
    layer_offsets_msg.data = static_cast<char*>(layer_offsets->data);
    layer_offsets_msg.size = layer_offsets.GetSize();
    // capture the array in the closure
    layer_offsets_msg.deallocator = [layer_offsets](Message*) {};
    CHECK_EQ(sender->Send(layer_offsets_msg, recv_id), ADD_SUCCESS);
    // send flow_offset
    Message flow_offsets_msg;
    flow_offsets_msg.data = static_cast<char*>(flow_offsets->data);
    flow_offsets_msg.size = flow_offsets.GetSize();
    // capture the array in the closure
    flow_offsets_msg.deallocator = [flow_offsets](Message*) {};
    CHECK_EQ(sender->Send(flow_offsets_msg, recv_id), ADD_SUCCESS);
    // send csr->indptr
    Message indptr_msg;
    indptr_msg.data = static_cast<char*>(indptr->data);
    indptr_msg.size = indptr.GetSize();
    // capture the array in the closure
    indptr_msg.deallocator = [indptr](Message*) {};
    CHECK_EQ(sender->Send(indptr_msg, recv_id), ADD_SUCCESS);
    // send csr->indices
    Message indices_msg;
    indices_msg.data = static_cast<char*>(indice->data);
    indices_msg.size = indice.GetSize();
    // capture the array in the closure
    indices_msg.deallocator = [indice](Message*) {};
    CHECK_EQ(sender->Send(indices_msg, recv_id), ADD_SUCCESS);
    // send csr->edge_ids
    Message edge_ids_msg;
    edge_ids_msg.data = static_cast<char*>(edge_ids->data);
    edge_ids_msg.size = edge_ids.GetSize();
    // capture the array in the closure
    edge_ids_msg.deallocator = [edge_ids](Message*) {};
    CHECK_EQ(sender->Send(edge_ids_msg, recv_id), ADD_SUCCESS);
  });

DGL_REGISTER_GLOBAL("network._CAPI_SenderSendSamplerEndSignal")
.set_body([] (DGLArgs args, DGLRetValue* rv) {
    CommunicatorHandle chandle = args[0];
    int recv_id = args[1];
    ArrayMeta meta(kFinalMsg);
    int64_t size = 0;
    char* data = meta.Serialize(&size);
    network::Sender* sender = static_cast<network::Sender*>(chandle);
    Message send_msg = {data, size};
    send_msg.deallocator = DefaultMessageDeleter;
    CHECK_EQ(sender->Send(send_msg, recv_id), ADD_SUCCESS);
  });

DGL_REGISTER_GLOBAL("network._CAPI_ReceiverRecvNodeFlow")
.set_body([] (DGLArgs args, DGLRetValue* rv) {
    CommunicatorHandle chandle = args[0];
    network::Receiver* receiver = static_cast<network::SocketReceiver*>(chandle);
    int send_id = 0;
    Message recv_msg;
    CHECK_EQ(receiver->Recv(&recv_msg, &send_id), REMOVE_SUCCESS);
    ArrayMeta meta(recv_msg.data, recv_msg.size);
    recv_msg.deallocator(&recv_msg);
    if (meta.msg_type() == kNodeFlowMsg) {
      CHECK_EQ(meta.ndarray_count() * 2, meta.data_shape_.size());
      NodeFlow nf = NodeFlow::Create();
      // node_mapping
      Message array_0;
      CHECK_EQ(receiver->RecvFrom(&array_0, send_id), REMOVE_SUCCESS);
      CHECK_EQ(meta.data_shape_[0], 1);
      nf->node_mapping = CreateNDArrayFromRaw(
        {meta.data_shape_[1]},
        DLDataType{kDLInt, 64, 1},
        DLContext{kDLCPU, 0},
        array_0.data);
      // edge_mapping
      Message array_1;
      CHECK_EQ(receiver->RecvFrom(&array_1, send_id), REMOVE_SUCCESS);
      CHECK_EQ(meta.data_shape_[2], 1);
      nf->edge_mapping = CreateNDArrayFromRaw(
        {meta.data_shape_[3]},
        DLDataType{kDLInt, 64, 1},
        DLContext{kDLCPU, 0},
        array_1.data);
      // layer_offset
      Message array_2;
      CHECK_EQ(receiver->RecvFrom(&array_2, send_id), REMOVE_SUCCESS);
      CHECK_EQ(meta.data_shape_[4], 1);
      nf->layer_offsets = CreateNDArrayFromRaw(
        {meta.data_shape_[5]},
        DLDataType{kDLInt, 64, 1},
        DLContext{kDLCPU, 0},
        array_2.data);
      // flow_offset
      Message array_3;
      CHECK_EQ(receiver->RecvFrom(&array_3, send_id), REMOVE_SUCCESS);
      CHECK_EQ(meta.data_shape_[6], 1);
      nf->flow_offsets = CreateNDArrayFromRaw(
        {meta.data_shape_[7]},
        DLDataType{kDLInt, 64, 1},
        DLContext{kDLCPU, 0},
        array_3.data);
      // CSR indptr
      Message array_4;
      CHECK_EQ(receiver->RecvFrom(&array_4, send_id), REMOVE_SUCCESS);
      CHECK_EQ(meta.data_shape_[8], 1);
      NDArray indptr = CreateNDArrayFromRaw(
        {meta.data_shape_[9]},
        DLDataType{kDLInt, 64, 1},
        DLContext{kDLCPU, 0},
        array_4.data);
      // CSR indice
      Message array_5;
      CHECK_EQ(receiver->RecvFrom(&array_5, send_id), REMOVE_SUCCESS);
      CHECK_EQ(meta.data_shape_[10], 1);
      NDArray indice = CreateNDArrayFromRaw(
        {meta.data_shape_[11]},
        DLDataType{kDLInt, 64, 1},
        DLContext{kDLCPU, 0},
        array_5.data);
      // CSR edge_ids
      Message array_6;
      CHECK_EQ(receiver->RecvFrom(&array_6, send_id), REMOVE_SUCCESS);
      CHECK_EQ(meta.data_shape_[12], 1);
      NDArray edge_ids = CreateNDArrayFromRaw(
        {meta.data_shape_[13]},
        DLDataType{kDLInt, 64, 1},
        DLContext{kDLCPU, 0},
        array_6.data);
      // Create CSR
      CSRPtr csr(new CSR(indptr, indice, edge_ids));
      nf->graph = GraphPtr(new ImmutableGraph(csr, nullptr));
      *rv = nf;
    } else if (meta.msg_type() == kFinalMsg) {
      *rv = meta.msg_type();
    } else {
      LOG(FATAL) << "Unknown message type: " << meta.msg_type();
    }
  });


////////////////////////// Distributed KVStore Components ////////////////////////////////


DGL_REGISTER_GLOBAL("network._CAPI_SenderSendKVMsg")
.set_body([] (DGLArgs args, DGLRetValue* rv) {
    int args_count = 0;
    CommunicatorHandle chandle = args[args_count++];
    int recv_id = args[args_count++];
    KVStoreMsg kv_msg;
    kv_msg.msg_type = args[args_count++];
    kv_msg.rank = args[args_count++];
    network::Sender* sender = static_cast<network::Sender*>(chandle);
    if (kv_msg.msg_type != kFinalMsg && kv_msg.msg_type != kBarrierMsg) {
      std::string name = args[args_count++];
      kv_msg.name = name;
      if (kv_msg.msg_type != kIPIDMsg) {
        kv_msg.id = args[args_count++];
      }
      if (kv_msg.msg_type != kPullMsg && kv_msg.msg_type != kIPIDMsg) {
        kv_msg.data = args[args_count++];
      }
    }
    int64_t kv_size = 0;
    char* kv_data = kv_msg.Serialize(&kv_size);
    // Send kv_data
    Message send_kv_msg;
    send_kv_msg.data = kv_data;
    send_kv_msg.size = kv_size;
    send_kv_msg.deallocator = DefaultMessageDeleter;
    CHECK_EQ(sender->Send(send_kv_msg, recv_id), ADD_SUCCESS);

    if (kv_msg.msg_type != kFinalMsg &&
        kv_msg.msg_type != kBarrierMsg &&
        kv_msg.msg_type != kIPIDMsg) {
      // Send ArrayMeta
      ArrayMeta meta(kv_msg.msg_type);
      meta.AddArray(kv_msg.id);
      if (kv_msg.msg_type != kPullMsg) {
        meta.AddArray(kv_msg.data);
      }
      int64_t meta_size = 0;
      char* meta_data = meta.Serialize(&meta_size);
      Message send_meta_msg;
      send_meta_msg.data = meta_data;
      send_meta_msg.size = meta_size;
      send_meta_msg.deallocator = DefaultMessageDeleter;
      CHECK_EQ(sender->Send(send_meta_msg, recv_id), ADD_SUCCESS);
      // Send ID NDArray
      Message send_id_msg;
      send_id_msg.data = static_cast<char*>(kv_msg.id->data);
      send_id_msg.size = kv_msg.id.GetSize();
      NDArray id = kv_msg.id;
      send_id_msg.deallocator = [id](Message*) {};
      CHECK_EQ(sender->Send(send_id_msg, recv_id), ADD_SUCCESS);
      // Send data NDArray
      if (kv_msg.msg_type != kPullMsg) {
        Message send_data_msg;
        send_data_msg.data = static_cast<char*>(kv_msg.data->data);
        send_data_msg.size = kv_msg.data.GetSize();
        NDArray data = kv_msg.data;
        send_data_msg.deallocator = [data](Message*) {};
        CHECK_EQ(sender->Send(send_data_msg, recv_id), ADD_SUCCESS);
      }
    }
  });

DGL_REGISTER_GLOBAL("network.CAPI_ReceiverRecvKVMsg")
.set_body([] (DGLArgs args, DGLRetValue* rv) {
    CommunicatorHandle chandle = args[0];
    network::Receiver* receiver = static_cast<network::SocketReceiver*>(chandle);
    KVStoreMsg *kv_msg = new KVStoreMsg();
    // Recv kv_Msg
    Message recv_kv_msg;
    int send_id;
    CHECK_EQ(receiver->Recv(&recv_kv_msg, &send_id), REMOVE_SUCCESS);
    kv_msg->Deserialize(recv_kv_msg.data, recv_kv_msg.size);
    recv_kv_msg.deallocator(&recv_kv_msg);
    if (kv_msg->msg_type == kFinalMsg ||
        kv_msg->msg_type == kBarrierMsg ||
        kv_msg->msg_type == kIPIDMsg) {
      *rv = kv_msg;
      return;
    }
    // Recv ArrayMeta
    Message recv_meta_msg;
    CHECK_EQ(receiver->RecvFrom(&recv_meta_msg, send_id), REMOVE_SUCCESS);
    ArrayMeta meta(recv_meta_msg.data, recv_meta_msg.size);
    recv_meta_msg.deallocator(&recv_meta_msg);
    // Recv ID NDArray
    Message recv_id_msg;
    CHECK_EQ(receiver->RecvFrom(&recv_id_msg, send_id), REMOVE_SUCCESS);
    CHECK_EQ(meta.data_shape_[0], 1);
    kv_msg->id = CreateNDArrayFromRaw(
      {meta.data_shape_[1]},
      DLDataType{kDLInt, 64, 1},
      DLContext{kDLCPU, 0},
      recv_id_msg.data);
    // Recv Data NDArray
    if (kv_msg->msg_type != kPullMsg) {
      Message recv_data_msg;
      CHECK_EQ(receiver->RecvFrom(&recv_data_msg, send_id), REMOVE_SUCCESS);
      CHECK_GE(meta.data_shape_[2], 1);
      std::vector<int64_t> vec_shape;
      for (int i = 3; i < meta.data_shape_.size(); ++i) {
        vec_shape.push_back(meta.data_shape_[i]);
      }
      kv_msg->data = CreateNDArrayFromRaw(
        vec_shape,
        DLDataType{kDLFloat, 32, 1},
        DLContext{kDLCPU, 0},
        recv_data_msg.data);
    }
    *rv = kv_msg;
  });

DGL_REGISTER_GLOBAL("network._CAPI_ReceiverGetKVMsgType")
.set_body([] (DGLArgs args, DGLRetValue* rv) {
    KVMsgHandle chandle = args[0];
    network::KVStoreMsg* msg = static_cast<KVStoreMsg*>(chandle);
    *rv = msg->msg_type;
  });

DGL_REGISTER_GLOBAL("network._CAPI_ReceiverGetKVMsgRank")
.set_body([] (DGLArgs args, DGLRetValue* rv) {
    KVMsgHandle chandle = args[0];
    network::KVStoreMsg* msg = static_cast<KVStoreMsg*>(chandle);
    *rv = msg->rank;
  });

DGL_REGISTER_GLOBAL("network._CAPI_ReceiverGetKVMsgName")
.set_body([] (DGLArgs args, DGLRetValue* rv) {
    KVMsgHandle chandle = args[0];
    network::KVStoreMsg* msg = static_cast<KVStoreMsg*>(chandle);
    *rv = msg->name;
  });

DGL_REGISTER_GLOBAL("network._CAPI_ReceiverGetKVMsgID")
.set_body([] (DGLArgs args, DGLRetValue* rv) {
    KVMsgHandle chandle = args[0];
    network::KVStoreMsg* msg = static_cast<KVStoreMsg*>(chandle);
    *rv = msg->id;
  });

DGL_REGISTER_GLOBAL("network._CAPI_ReceiverGetKVMsgData")
.set_body([] (DGLArgs args, DGLRetValue* rv) {
    KVMsgHandle chandle = args[0];
    network::KVStoreMsg* msg = static_cast<KVStoreMsg*>(chandle);
    *rv = msg->data;
  });

}  // namespace network
}  // namespace dgl
