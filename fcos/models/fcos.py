import math
import numpy as np
import torch.nn as nn
import torch.nn.functional as F
import torch
import torchvision
from torchvision.transforms import Normalize
from typing import List, Tuple

from .backbone import Backbone

CLASSES = [
    "BACKGROUND",
    "CAR",
]


class FCOS(nn.Module):
    classes = CLASSES
    strides = [8, 16, 32, 64, 128]

    def __init__(self):
        super(FCOS, self).__init__()

        self.backbone = Backbone()
        self.scales = nn.Parameter(torch.FloatTensor([8, 16, 32, 64, 128]))

        # Feature Pyramid Network
        self.layer_1_to_p3 = nn.Conv2d(512, 256, kernel_size=3, padding=1)
        self.layer_2_to_p4 = nn.Conv2d(1024, 256, kernel_size=3, padding=1)
        self.layer_3_to_p5 = nn.Conv2d(2048, 256, kernel_size=3, padding=1)
        self.p5_to_p6 = nn.Conv2d(256, 256, kernel_size=3, padding=1, stride=2)
        self.p6_to_p7 = nn.Conv2d(256, 256, kernel_size=3, padding=1, stride=2)

        self.classification_head = nn.Sequential(
            nn.Conv2d(256, 256, kernel_size=3, padding=1),
            nn.GroupNorm(32, 256),
            nn.ReLU(inplace=True),
            nn.Conv2d(256, 256, kernel_size=3, padding=1),
            nn.GroupNorm(32, 256),
            nn.ReLU(inplace=True),
            nn.Conv2d(256, 256, kernel_size=3, padding=1),
            nn.GroupNorm(32, 256),
            nn.ReLU(inplace=True),
            nn.Conv2d(256, 256, kernel_size=3, padding=1),
            nn.GroupNorm(32, 256),
            nn.ReLU(inplace=True),
            # nn.BatchNorm2d(256),
        )

        self.classification_to_class = nn.Sequential(
            nn.Conv2d(256, len(CLASSES), kernel_size=3, padding=1),
            # nn.ReLU(inplace=True),
        )

        self.classification_to_centerness = nn.Sequential(
            nn.Conv2d(256, 1, kernel_size=3, padding=1),
            # nn.ReLU(inplace=True),
        )

        self.regression_head = nn.Sequential(
            nn.Conv2d(256, 256, kernel_size=3, padding=1),
            nn.GroupNorm(32, 256),
            nn.ReLU(inplace=True),
            nn.Conv2d(256, 256, kernel_size=3, padding=1),
            nn.GroupNorm(32, 256),
            nn.ReLU(inplace=True),
            nn.Conv2d(256, 256, kernel_size=3, padding=1),
            nn.GroupNorm(32, 256),
            nn.ReLU(inplace=True),
            nn.Conv2d(256, 256, kernel_size=3, padding=1),
            nn.GroupNorm(32, 256),
            nn.ReLU(inplace=True),
            nn.Conv2d(256, 4, kernel_size=3, padding=1),
        )

        for modules in [self.regression_head, self.classification_to_centerness,
                        self.classification_to_class, self.classification_head,
                        self.layer_1_to_p3,
                        self.layer_2_to_p4,
                        self.layer_3_to_p5,
                        self.p5_to_p6,
                        self.p6_to_p7]:
            for l in modules.modules():
                if isinstance(l, nn.Conv2d):
                    torch.nn.init.normal_(l.weight, std=0.01)
                    torch.nn.init.constant_(l.bias, 0)

    def freeze_backbone(self):
        for param in self.backbone.parameters():
            param.requires_grad = False

    def unfreeze_backbone(self):
        for param in self.backbone.parameters():
            param.requires_grad = True

    def forward(
        self, x: torch.FloatTensor
    ) -> Tuple[List[torch.FloatTensor], List[torch.FloatTensor], List[torch.FloatTensor]]:
        """
        Takes as input a batch of images encoded as BCHW, which have been normalized using
        the function `fcos.models.normalize_batch`.

        Returns a list of tuples, where each entry in the list represents one of the levels of the five
        feature maps. The tuple contains respectively, 
        - A float tensor indexed as BHWC, where C represents the 1-hot encoded class labels.
        - A float tensor indexed as BHW[x_min, y_min, x_max, y_max], where the values correspond
          directly with the input tensor, x
        """
        _, _, img_height, img_width = x.shape

        layer_1, layer_2, layer_3 = self.backbone(x)

        p5 = self.layer_3_to_p5(layer_3)
        p4 = self.layer_2_to_p4(layer_2) + _upsample(p5)
        p3 = self.layer_1_to_p3(layer_1) + _upsample(p4)
        p6 = self.p5_to_p6(p5)
        p7 = self.p6_to_p7(p6)

        feature_pyramid = [p3, p4, p5, p6, p7]

        classes_by_feature = []
        centerness_by_feature = []
        reg_by_feature = []

        for scale, stride, feature in zip(self.scales, self.strides, feature_pyramid):
            classification = self.classification_head(feature)
            classes = self.classification_to_class(classification).sigmoid()
            centerness = self.classification_to_centerness(classification).sigmoid()
            reg = torch.exp(self.regression_head(feature))

            # B[C]HW  -> BHW[C]
            classes = classes.permute(0, 2, 3, 1).contiguous()
            centerness = centerness.permute(0, 2, 3, 1).contiguous().squeeze()
            reg = reg.permute(0, 2, 3, 1).contiguous()

            reg_by_feature.append(reg)
            centerness_by_feature.append(centerness)
            classes_by_feature.append(classes)

        return classes_by_feature, centerness_by_feature, reg_by_feature


def normalize_batch(x: torch.FloatTensor) -> torch.FloatTensor:
    """
    Given a tensor representing a batch of unnormalized B[RGB]HW images,
    where RGB are floating point values in the range 0 to 255, prepare the tensor for the
    FCOS network. This has been defined to match the backbone resnet50 network.
    See https://pytorch.org/docs/master/torchvision/models.html for more details.
    """
    f = Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])

    for b in range(x.shape[0]):
        f(x[b])

    return x



def _upsample(x):
    return F.interpolate(x, size=(x.shape[2] * 2, x.shape[3] * 2), mode="bilinear", align_corners=True)
