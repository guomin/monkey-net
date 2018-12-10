from torch import nn
import torch
import torch.nn.functional as F
from modules.util import make_coordinate_grid
from modules.keypoint_detector import kp2gaussian


class MovementEmbeddingModule(nn.Module):
    """
    Produce a keypoint representation that will be further used by other modules
    """

    def __init__(self, num_kp, kp_variance, num_channels, use_deformed_appearance=False, use_difference=False,
                 use_heatmap=True, add_bg_feature_map=False, heatmap_type='gaussian', norm_const=100):
        super(MovementEmbeddingModule, self).__init__()

        assert heatmap_type in ['gaussian', 'difference']

        assert ((int(use_heatmap) + int(use_deformed_appearance) + int(use_difference)) >= 1)

        self.out_channels = (1 * use_heatmap + 2 * use_difference + num_channels * use_deformed_appearance) * (
        num_kp + add_bg_feature_map)

        self.kp_variance = kp_variance
        self.heatmap_type = heatmap_type
        self.use_difference = use_difference
        self.use_deformed_appearance = use_deformed_appearance
        self.use_heatmap = use_heatmap
        self.add_bg_feature_map = add_bg_feature_map
        self.norm_const = norm_const

    def normalize_heatmap(self, heatmap):
        if self.norm_const == "sum":
            heatmap_shape = heatmap.shape
            heatmap = heatmap.view(heatmap_shape[0], heatmap_shape[1], heatmap_shape[2], -1)
            heatmap = heatmap / heatmap.sum(dim=3, keepdim=True)
            return heatmap.view(*heatmap_shape)
        else:
            return heatmap / self.norm_const

    def forward(self, appearance_frame, kp_video, kp_appearance):
        spatial_size = appearance_frame.shape[3:]

        bs, _, _, h, w = appearance_frame.shape
        _, d, num_kp, _ = kp_video['mean'].shape

        inputs = []
        if self.use_heatmap:
            heatmap = self.normalize_heatmap(
                kp2gaussian(kp_video, spatial_size=spatial_size, kp_variance=self.kp_variance))
            if self.heatmap_type == 'difference':
                heatmap_appearance = self.normalize_heatmap(
                    kp2gaussian(kp_appearance, spatial_size=spatial_size, kp_variance=self.kp_variance))
                heatmap = heatmap - heatmap_appearance
            if self.add_bg_feature_map:
                zeros = torch.zeros(bs, d, 1, h, w).type(heatmap.type())
                heatmap = torch.cat([zeros, heatmap], dim=2)
            heatmap = heatmap.unsqueeze(3)
            inputs.append(heatmap)

        num_kp += self.add_bg_feature_map
        if self.use_difference or self.use_deformed_appearance:
            kp_video_diff = kp_appearance['mean'] - kp_video['mean']
            if self.add_bg_feature_map:
                zeros = torch.zeros(bs, d, 1, 2).type(kp_video_diff.type())
                kp_video_diff = torch.cat([zeros, kp_video_diff], dim=2)
            kp_video_diff = kp_video_diff.view((bs, d, num_kp, 2, 1, 1)).repeat(1, 1, 1, 1, h, w)

        if self.use_difference:
            inputs.append(kp_video_diff)

        if self.use_deformed_appearance:
            appearance_repeat = appearance_frame.unsqueeze(1).unsqueeze(1).repeat(1, d, num_kp, 1, 1, 1, 1)
            appearance_repeat = appearance_repeat.view(bs * d * num_kp, -1, h, w)

            deformation_approx = kp_video_diff.view((bs * d * num_kp, -1, h, w)).permute(0, 2, 3, 1)
            coordinate_grid = make_coordinate_grid((h, w), type=deformation_approx.type())
            coordinate_grid = coordinate_grid.view(1, h, w, 2)
            deformation_approx = coordinate_grid + deformation_approx

            appearance_approx_deform = F.grid_sample(appearance_repeat, deformation_approx)
            appearance_approx_deform = appearance_approx_deform.view((bs, d, num_kp, -1, h, w))
            inputs.append(appearance_approx_deform)

        movement_encoding = torch.cat(inputs, dim=3)
        movement_encoding = movement_encoding.view(bs, d, -1, h, w)

        return movement_encoding.permute(0, 2, 1, 3, 4)
