from sklearn import metrics
from torch.utils.data.dataset import Dataset
from torch.utils.data.dataloader import DataLoader
import preprocess
import torch
from torch import nn


class Mol_dataset(Dataset):
    def __init__(self, data):
        self.data = data

    def __getitem__(self, index):
        return self.data[index]

    def __len__(self):
        return len(self.data)


Mol_dataloader = DataLoader(Mol_dataset, batch_size=3, shuffle=True)
for temp in Mol_dataloader:
    print(temp)

loss_func = nn.BCEWithLogitsLoss()
metric_func = metrics.roc_auc_score()
train, val, test = preprocess.main('Data/bbbp_test.csv')
pass
