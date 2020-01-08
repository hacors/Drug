from sklearn import metrics
from torch.utils.data.dataset import Dataset
import preprocess
import torch
from torch import nn


class csv_dataset(Dataset):
    def __init__(self, data):
        self.data = data

    def __getitem__(self, index):
        pass

    def __len__(self):
        return len(self.data)


loss_func = nn.BCEWithLogitsLoss()
metric_func = metrics.roc_auc_score()
train, val, test = preprocess.main('Data/bbbp_test.csv')
pass
