from architecture.models import *
from dataset.datasets import FullPartDatasetMenu
from dataset.index import HierarchicalIndexTree  # Needed for pickle
from dataset.transforms import *
from lightning.trainer import LightningTrainer
from util.strings import banner, set_logging_to_stdout
from util.torch.nn import set_determinsitic_run
from util.torch.data import none_or_int, none_or_str

set_logging_to_stdout()
set_determinsitic_run()  # Set a universal random seed


# ----------------------------------------------------------------------------------------------------------------------
#                                               Main Arguments
# ----------------------------------------------------------------------------------------------------------------------
def parser():
    p = HyperOptArgumentParser(strategy='random_search')

    # Check-pointing
    p.add_argument('--exp_name', type=str, default='test_code', help='The experiment name. Leave empty for default')
    # TODO - Don't forget to change me!
    p.add_argument('--version', type=none_or_int, default=7,
                   help='Weights will be saved at weight_dir=exp_name/version_{version}. '
                        'Use None to automatically choose an unused version')
    p.add_argument('--resume_cfg', nargs=2, type=bool, default=(False, True),
                   help='Only works if version != None and and weight_dir exists. '
                        '1st Bool: Whether to attempt restore of early stopping callback. '
                        '2nd Bool: Whether to attempt restore learning rate scheduler')
    p.add_argument('--save_completions', type=int, choices=[0, 1, 2, 3], default=3,
                   help='Use 0 for no save. Use 1 for vertex only save in obj file. Use 2 for a full mesh save (v&f). '
                        'Use 3 for gt,tp,gt_part,tp_part save as well.')

    # Dataset Config:
    # NOTE: A well known ML rule: double the learning rate if you double the batch size.
    p.add_argument('--batch_size', type=int, default=3, help='SGD batch size')
    p.add_argument('--counts', nargs=3, type=none_or_int, default=(10, 10, 10),
                   help='[Train,Validation,Test] number of samples. Use None for all in partition')
    p.add_argument('--in_channels', choices=[3, 6, 12], default=6,
                   help='Number of input channels')

    # Train Config:
    p.add_argument('--force_train_epoches', type=int, default=1,
                   help="Force train for this amount. Usually we'd early stop using the callback. Use 1 to disable")
    p.add_argument('--max_epochs', type=int, default=100,
                   help='Maximum epochs to train for')
    p.add_argument('--lr', type=float, default=0.001, help='The learning step to use')

    # Optimizer
    p.add_argument("--weight_decay", type=float, default=0, help="Adam's weight decay - usually use 1e-4")
    p.add_argument("--plateau_patience", type=none_or_int, default=None,
                   help="Number of epoches to wait on learning plateau before reducing step size. Use None to shut off")
    p.add_argument("--early_stop_patience", type=int, default=100,  # TODO - Remember to setup resume_cfg correctly
                   help="Number of epoches to wait on learning plateau before stopping train")
    # Without early stop callback, we'll train for cfg.MAX_EPOCHS

    # L2 Losses: Use 0 to ignore, >0 to lightning
    p.add_argument('--lambdas', nargs=7, type=float, default=(1, 0, 0, 0, 0, 0, 0),
                   help='[XYZ,Normal,Moments,EuclidDistMat,EuclidNormalDistMap,FaceAreas,Volume]'
                        'loss multiplication modifiers')
    p.add_argument('--mask_penalties', nargs=7, type=float, default=(0, 0, 0, 0, 0, 0, 0),
                   help='[XYZ,Normal,Moments,EuclidDistMat,EuclidNormalDistMap,FaceAreas,Volume]'
                        'increased weight on mask vertices. Use val <= 1 to disable')
    p.add_argument('--dist_v_penalties', nargs=7, type=float, default=(0, 0, 0, 0, 0, 0, 0),
                   help='[XYZ,Normal,Moments,EuclidDistMat,EuclidNormalDistMap, FaceAreas, Volume]'
                        'increased weight on distant vertices. Use val <= 1 to disable')
    p.add_argument('--loss_class', type=str, choices=['BasicLoss', 'SkepticLoss'], default='BasicLoss',
                   help='The loss class')  # TODO - generalize this

    # Computation
    p.add_argument('--gpus', type=none_or_int, default=-1, help='Use -1 to use all available. Use None to run on CPU')
    p.add_argument('--distributed_backend', type=str, default='dp', help='supports three options dp, ddp, ddp2')
    # TODO - ddp2,ddp Untested

    # Visualization
    p.add_argument('--use_auto_tensorboard', type=bool, default=3,
                   help='Mode: 0 - Does nothing. 1 - Opens up only server. 2 - Opens up only chrome. 3- Opens up both '
                        'chrome and server')
    p.add_argument('--plotter_class', type=none_or_str, choices=[None, 'CompletionPlotter'],
                   default='CompletionPlotter',
                   help='The plotter class or None for no plot')  # TODO - generalize this

    return [p]


# ----------------------------------------------------------------------------------------------------------------------
#                                                   Mains
# ----------------------------------------------------------------------------------------------------------------------
def train_main():
    banner('Network Init')
    nn = F2PEncoderDecoder(parser())
    nn.identify_system()

    # Bring in data:
    ds = FullPartDatasetMenu.get('FaustPyProj')
    ldrs1 = ds.loaders(split=[0.8, 0.1, 0.1], s_nums=nn.hp.counts, s_shuffle=[True] * 3, s_transform=[Center()] * 3,
                       batch_size=nn.hp.batch_size, device=nn.hp.dev, n_channels=nn.hp.in_channels, method='f2p',
                       s_dynamic=[False] * 3)
    ds = FullPartDatasetMenu.get('DFaustPyProj')
    ldrs2 = ds.loaders(split=[0.7, 0.3], s_nums=[20, 20], s_shuffle=[True] * 2, s_transform=[Center()] * 2,
                       batch_size=nn.hp.batch_size, device=nn.hp.dev, n_channels=nn.hp.in_channels, method='rand_f2p',
                       s_dynamic=[False] * 2)

    # Supply the network with the loaders:
    trainer = LightningTrainer(nn, [ldrs1[0], [ldrs1[1], ldrs2[0]], [ldrs1[2], ldrs2[1]]])
    # trainer = LightningTrainer(nn,ldrs1)
    trainer.train()
    trainer.test()
    trainer.finalize()


if __name__ == '__main__':
    train_main()
