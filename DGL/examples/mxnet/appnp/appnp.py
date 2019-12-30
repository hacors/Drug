import argparse, time
import numpy as np
import dgl
import mxnet as mx
from mxnet import nd, gluon
from mxnet.gluon import nn
from dgl import DGLGraph
from dgl.data import register_data_args, load_data
from dgl.nn.mxnet.conv import APPNPConv

class APPNP(nn.Block):
    def __init__(self,
                 g,
                 in_feats,
                 hiddens,
                 n_classes,
                 activation,
                 feat_drop,
                 edge_drop,
                 alpha,
                 k):
        super(APPNP, self).__init__()
        self.g = g

        with self.name_scope():
            self.layers = nn.Sequential()
            # input layer
            self.layers.add(nn.Dense(hiddens[0], in_units=in_feats))
            # hidden layers
            for i in range(1, len(hiddens)):
                self.layers.add(nn.Dense(hiddens[i], in_units=hiddens[i - 1]))
            # output layer
            self.layers.add(nn.Dense(n_classes, in_units=hiddens[-1]))
            self.activation = activation
            if feat_drop:
                self.feat_drop = nn.Dropout(feat_drop)
            else:
                self.feat_drop = lambda x: x
            self.propagate = APPNPConv(k, alpha, edge_drop)

    def forward(self, features):
        # prediction step
        h = features
        h = self.feat_drop(h)
        h = self.activation(self.layers[0](h))
        for layer in self.layers[1:-1]:
            h = self.activation(layer(h))
        h = self.layers[-1](self.feat_drop(h))
        # propagation step
        h = self.propagate(self.g, h)
        return h

def evaluate(model, features, labels, mask):
    pred = model(features).argmax(axis=1)
    accuracy = ((pred == labels) * mask).sum() / mask.sum().asscalar()
    return accuracy.asscalar()

def main(args):
    # load and preprocess dataset
    data = load_data(args)
    features = nd.array(data.features)
    labels = nd.array(data.labels)
    train_mask = nd.array(data.train_mask)
    val_mask = nd.array(data.val_mask)
    test_mask = nd.array(data.test_mask)

    in_feats = features.shape[1]
    n_classes = data.num_labels
    n_edges = data.graph.number_of_edges()
    print("""----Data statistics------'
      #Edges %d
      #Classes %d
      #Train samples %d
      #Val samples %d
      #Test samples %d""" %
          (n_edges, n_classes,
           train_mask.sum().asscalar(),
           val_mask.sum().asscalar(),
           test_mask.sum().asscalar()))

    if args.gpu < 0:
        ctx = mx.cpu()
    else:
        ctx = mx.gpu(args.gpu)

    features = features.as_in_context(ctx)
    labels = labels.as_in_context(ctx)
    train_mask = train_mask.as_in_context(ctx)
    val_mask = val_mask.as_in_context(ctx)
    test_mask = test_mask.as_in_context(ctx)

    # graph preprocess and calculate normalization factor
    g = DGLGraph(data.graph)
    n_edges = g.number_of_edges()
    # add self loop
    g.add_edges(g.nodes(), g.nodes())
    g.set_n_initializer(dgl.init.zero_initializer)
    g.set_e_initializer(dgl.init.zero_initializer)

    # create APPNP model
    model = APPNP(g,
                  in_feats,
                  args.hidden_sizes,
                  n_classes,
                  nd.relu,
                  args.in_drop,
                  args.edge_drop,
                  args.alpha,
                  args.k)

    model.initialize(ctx=ctx)
    n_train_samples = train_mask.sum().asscalar()
    loss_fcn = gluon.loss.SoftmaxCELoss()

    # use optimizer
    print(model.collect_params())
    trainer = gluon.Trainer(model.collect_params(), 'adam',
            {'learning_rate': args.lr, 'wd': args.weight_decay})

    # initialize graph
    dur = []
    for epoch in range(args.n_epochs):
        if epoch >= 3:
            t0 = time.time()
        # forward
        with mx.autograd.record():
            pred = model(features)
            loss = loss_fcn(pred, labels, mx.nd.expand_dims(train_mask, 1))
            loss = loss.sum() / n_train_samples

        loss.backward()
        trainer.step(batch_size=1)

        if epoch >= 3:
            loss.asscalar()
            dur.append(time.time() - t0)
            acc = evaluate(model, features, labels, val_mask)
            print("Epoch {:05d} | Time(s) {:.4f} | Loss {:.4f} | Accuracy {:.4f} | "
                  "ETputs(KTEPS) {:.2f}". format(
                epoch, np.mean(dur), loss.asscalar(), acc, n_edges / np.mean(dur) / 1000))

    # test set accuracy
    acc = evaluate(model, features, labels, test_mask)
    print("Test accuracy {:.2%}".format(acc))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='APPNP')
    register_data_args(parser)
    parser.add_argument("--in-drop", type=float, default=0.5,
                        help="input feature dropout")
    parser.add_argument("--edge-drop", type=float, default=0.5,
                        help="edge propagation dropout")
    parser.add_argument("--gpu", type=int, default=-1,
                        help="gpu")
    parser.add_argument("--lr", type=float, default=1e-2,
                        help="learning rate")
    parser.add_argument("--n-epochs", type=int, default=200,
                        help="number of training epochs")
    parser.add_argument("--hidden_sizes", type=int, nargs='+', default=[64],
                        help="hidden unit sizes for appnp")
    parser.add_argument("--k", type=int, default=10,
                        help="Number of propagation steps")
    parser.add_argument("--alpha", type=float, default=0.1,
                        help="Teleport Probability")
    parser.add_argument("--weight-decay", type=float, default=5e-4,
                        help="Weight for L2 loss")
    args = parser.parse_args()
    print(args)

    main(args)