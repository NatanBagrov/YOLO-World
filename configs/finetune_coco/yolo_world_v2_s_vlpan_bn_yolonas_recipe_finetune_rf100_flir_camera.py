_base_ = "../../third_party/mmyolo/configs/yolov8/" "yolov8_s_syncbn_fast_8xb16-500e_coco.py"
custom_imports = dict(imports=["yolo_world"], allow_failed_imports=False)

dataset_name = "flir-camera-objects"
num_classes = 4
metainfo = dict(classes=["bicycle", "car", "dog", "person"])

# hyper-parameters
num_training_classes = num_classes
max_epochs = 100  # Maximum training epochs
close_mosaic_epochs = 80
save_epoch_intervals = 5
text_channels = 512
neck_embed_channels = [128, 256, _base_.last_stage_out_channels // 2]
neck_num_heads = [4, 8, _base_.last_stage_out_channels // 2 // 32]
base_lr = 5e-4
weight_decay = 0.0001
train_batch_size_per_gpu = 16
load_from = "../checkpoints/yolo_world_v2_s_obj365v1_goldg_pretrain-55b943ea.pth"
text_model_name = "openai/clip-vit-base-patch32"
persistent_workers = False
mixup_prob = 0.0

# model settings
model = dict(
    type="YOLOWorldDetector",
    mm_neck=True,
    num_train_classes=num_training_classes,
    num_test_classes=num_classes,
    data_preprocessor=dict(type="YOLOWDetDataPreprocessor"),
    backbone=dict(
        _delete_=True,
        type="MultiModalYOLOBackbone",
        image_model={{_base_.model.backbone}},
        text_model=dict(type="HuggingCLIPLanguageBackbone", model_name=text_model_name, frozen_modules=["all"]),
    ),
    neck=dict(
        type="YOLOWorldPAFPN",
        guide_channels=text_channels,
        embed_channels=neck_embed_channels,
        num_heads=neck_num_heads,
        block_cfg=dict(type="MaxSigmoidCSPLayerWithTwoConv"),
    ),
    bbox_head=dict(
        type="YOLOWorldHead",
        head_module=dict(type="YOLOWorldHeadModule", use_bn_head=True, embed_dims=text_channels, num_classes=num_training_classes),
        loss_bbox=dict(_delete_=True, type="IoULoss", iou_mode="giou", bbox_format="xyxy", reduction="sum", loss_weight=7.5, return_iou=False),
        loss_cls=dict(_delete_=True, type="mmdet.VarifocalLoss", use_sigmoid=True, alpha=0.75, gamma=2.0, iou_weighted=True, reduction="sum", loss_weight=1.0),
    ),
    train_cfg=dict(
        assigner=dict(
            _delete_=True,
            type="BatchTaskAlignedAssigner",
            num_classes=num_classes,
            use_ciou=True,
            topk=13,
            alpha=1,
            beta=6,
            eps=1e-9,
        ),
    ),
)

# dataset settings
text_transform = [
    dict(type="RandomLoadText", num_neg_samples=(num_classes, num_classes), max_num_samples=num_training_classes, padding_to_max=True, padding_value=""),
    dict(type="mmdet.PackDetInputs", meta_keys=("img_id", "img_path", "ori_shape", "img_shape", "flip", "flip_direction", "texts")),
]
mosaic_affine_transform = [
    dict(type="MultiModalMosaic", img_scale=_base_.img_scale, pad_val=114.0, pre_transform=_base_.pre_transform),
    dict(type="YOLOv5CopyPaste", prob=0.1),
    dict(
        type="YOLOv5RandomAffine",
        max_rotate_degree=0.0,
        max_shear_degree=0.0,
        max_aspect_ratio=20.0,
        scaling_ratio_range=(0.5, 1.5),
        # img_scale is (width, height)
        border=(-_base_.img_scale[0] // 2, -_base_.img_scale[1] // 2),
        border_val=(114, 114, 114),
    ),
]
train_pipeline = [
    *_base_.pre_transform,
    *mosaic_affine_transform,
    dict(type="YOLOv5MultiModalMixUp", prob=mixup_prob, pre_transform=[*_base_.pre_transform, *mosaic_affine_transform]),
    *_base_.last_transform[:-1],
    *text_transform,
]
train_pipeline_stage2 = [*_base_.train_pipeline_stage2[:-1], *text_transform]

coco_train_dataset = dict(
    _delete_=True,
    type="MultiModalDataset",
    dataset=dict(
        type="YOLOv5CocoDataset",
        data_root=f"/data/rf100/{dataset_name}",
        ann_file="train/_annotations.coco.json",
        data_prefix=dict(img="train/"),
        filter_cfg=dict(filter_empty_gt=False, min_size=32),
        metainfo=metainfo,
    ),
    class_text_path=f"../data/texts/rf100_{dataset_name}_class_texts.json",
    pipeline=train_pipeline,
)

train_dataloader = dict(
    persistent_workers=persistent_workers, batch_size=train_batch_size_per_gpu, collate_fn=dict(type="yolow_collate"), dataset=coco_train_dataset
)
test_pipeline = [
    *_base_.test_pipeline[:-1],
    dict(type="LoadText"),
    dict(type="mmdet.PackDetInputs", meta_keys=("img_id", "img_path", "ori_shape", "img_shape", "scale_factor", "pad_param", "texts")),
]
coco_val_dataset = dict(
    _delete_=True,
    type="MultiModalDataset",
    dataset=dict(
        type="YOLOv5CocoDataset",
        data_root=f"/data/rf100/{dataset_name}",
        ann_file="valid/_annotations.coco.json",
        data_prefix=dict(img="valid/"),
        metainfo=metainfo,
    ),
    class_text_path=f"../data/texts/rf100_{dataset_name}_class_texts.json",
    pipeline=test_pipeline,
)

val_dataloader = dict(dataset=coco_val_dataset)
test_dataloader = val_dataloader
# training settings
default_hooks = dict(
    param_scheduler=dict(scheduler_type="cosine", lr_factor=0.01, max_epochs=max_epochs),
    checkpoint=dict(max_keep_ckpts=3, save_best=None, interval=save_epoch_intervals),
)
custom_hooks = [
    dict(type="EMAHook", ema_type="ExpMomentumEMA", momentum=0.1, update_buffers=True, strict_load=False, priority=49),
    dict(type="mmdet.PipelineSwitchHook", switch_epoch=max_epochs - close_mosaic_epochs, switch_pipeline=train_pipeline_stage2),
]
train_cfg = dict(max_epochs=max_epochs, val_interval=5, dynamic_intervals=[((max_epochs - close_mosaic_epochs), _base_.val_interval_stage2)])
optim_wrapper = dict(
    optimizer=dict(
        _delete_=True,
        type="AdamW",
        lr=base_lr,
        weight_decay=weight_decay,
        batch_size_per_gpu=train_batch_size_per_gpu,
    ),
    paramwise_cfg=dict(custom_keys={"backbone.text_model": dict(lr_mult=0.01), "logit_scale": dict(weight_decay=0.0)}),
    constructor="YOLOWv5OptimizerConstructor",
)

# evaluation settings
val_evaluator = dict(
    _delete_=True,
    type="mmdet.CocoMetric",
    proposal_nums=(100, 1, 10),
    ann_file=f"/data/rf100/{dataset_name}/valid/_annotations.coco.json",
    metric="bbox",
)

vis_backends = [
    dict(type="LocalVisBackend"),
    dict(type="WandbVisBackend", init_kwargs={"project": "yolo-world", "entity": "algo", "name": f"finetune_s_rf100_{dataset_name}_yolonas_tal_giou_vf"}),
]

visualizer = dict(type="mmdet.DetLocalVisualizer", vis_backends=vis_backends, name="visualizer")
log_processor = dict(type="LogProcessor", window_size=4, by_epoch=True)

randomness = dict(seed=42, diff_rank_seed=True, deterministic=False)  # deterministic does not work with the CUBLAS we have installed
