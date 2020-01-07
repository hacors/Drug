from sklearn import metrics

import preprocess
import torch
from torch import nn

loss_func = nn.BCEWithLogitsLoss()
metric_func = metrics.roc_auc_score()
