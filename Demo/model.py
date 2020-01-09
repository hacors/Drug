from sklearn import metrics
from torch.utils.data.dataset import Dataset
from torch.utils.data.dataloader import DataLoader
from torchvision import transforms
import preprocess
import torch
from torch import nn


class Mol_dataset(Dataset):
    def __init__(self, data):
        self.data = data

    def __getitem__(self, index):
        transform_data = torch.tensor(self.data[index])
        return transform_data

    def __len__(self):
        return len(self.data)


train, val, test = preprocess.main('Data/bbbp_test.csv')
Mol_dataset_true = Mol_dataset(train)
Mol_dataloader = DataLoader(
    Mol_dataset_true, batch_size=3, shuffle=True)
for temp in Mol_dataloader:
    pass
    print(temp)

loss_func = nn.BCEWithLogitsLoss()
metric_func = metrics.roc_auc_score()
train, val, test = preprocess.main('Data/bbbp_test.csv')
pass
