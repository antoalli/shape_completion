from __future__ import print_function
import argparse
import random
# import numpy as np
import torch
import torch.optim as optim
import os
import json
import visdom
import time
import matplotlib.pyplot as plt
import scipy.io as sio
import scipy
from dataset import *
from model import *
from utils import *
import visdom


def main():
    # OH: Wrapping the main code with __main__ check is necessary for Windows compatibility
    # of the multi-process data loader (see pytorch documentation)
    # =============PARAMETERS======================================== #
    parser = argparse.ArgumentParser()
    # Learning params
    parser.add_argument('--batchSize', type=int, default=8, help='input batch size')
    parser.add_argument('--workers', type=int, help='number of data loading workers', default=8)
    parser.add_argument('--nepoch', type=int, default=1000, help='number of epochs to train for')

    # folder where to take pre-trained parameters
    parser.add_argument('--model_dir', type=str, default='',
                        help='optional reload model directory')
    parser.add_argument('--model_file', type=str, default='',
                        help='optional reload model file in model directory')
    # folder that stores the log for the run
    parser.add_argument('--save_path', type=str, default='Exp18_TrainingWithDFaust_LossWithNormals',
                        help='save path')
    # parser.add_argument('--env', type=str, default="shape_completion", help='visdom environment')  # OH: TODO edit

    # Network params
    parser.add_argument('--num_input_channels', type=int, default=12)
    parser.add_argument('--num_output_channels', type=int,
                        default=3)  # We assume the network return predicted xyz as 3 channels
    parser.add_argument('--use_same_subject', type=bool, default=True)
    # OH: a flag wether to use the same subject in AMASS examples (or two different subjects)
    parser.add_argument('--centering', type=bool, default=True)
    # OH: indicating whether the shapes are centerd w.r.t center of mass before entering the network

    # Dataset params - Ido - Don't think these work
    parser.add_argument('--amass_train_size', type=int, default=10000)
    parser.add_argument('--amass_validation_size', type=int, default=10000)
    parser.add_argument('--amass_test_size', type=int, default=200)
    parser.add_argument('--dynamic_faust_train_size', type=int, default=10000)
    parser.add_argument('--dynamic_faust_test_size', type=int, default=10000)
    parser.add_argument('--faust_train_size', type=int, default=8000)
    parser.add_argument('--faust_test_size', type=int, default=2000)
    parser.add_argument('--p2p_faust_train_size', type=int, default=80000) #part 2 part dataloader
    parser.add_argument('--p2p_faust_test_size', type=int, default=20000)
    parser.add_argument('--filtering', type=float, default=0.09, help='amount of filtering to apply on l2 distances')

    # Losses: Use 0 to ignore the specific loss, and val > 0 to compute it. The higher the value, the more weight the loss is
    parser.add_argument('--normal_loss_slope', type=int, default=0.1)
    parser.add_argument('--euclid_dist_loss_slope', type=int, default=0)  # Warning - Requires a lot of memory!
    parser.add_argument('--distant_vertex_loss_slope', type=int, default=1)

    # Adjustments to the XYZ  and Normal losses
    parser.add_argument('--mask_xyz_penalty', type=int, default=1, help='Penalize only the mask values on xyz loss')
    parser.add_argument('--use_mask_normal_penalty', type=bool, default=True,
                        help='Penalize only the mask values on normal loss')
    parser.add_argument('--use_mask_normal_distant_vertex_penalty', type=bool, default=True)

    parser.add_argument('--use_visdom', type=bool, default=True)

    opt = parser.parse_args()
    print(opt)

    assert opt.faust_train_size <= 8000
    # =============DEFINE stuff for logs ======================================== #
    # Launch visdom for visualization
    if opt.use_visdom:
        vis = visdom.Visdom(port=8888, env=opt.save_path)
    save_path = opt.save_path
    if not os.path.exists("log"):
        os.mkdir("log")
    dir_name = os.path.join('./log/', save_path)
    if not os.path.exists(dir_name):
        os.mkdir(dir_name)
    logname = os.path.join(dir_name, 'log.txt')
    with open(logname, 'a') as f:  # open and append
        f.write(str(opt) + '\n')

    opt.manualSeed = 1  # random.randint(1, 10000)  # fix seed
    print("Random Seed: ", opt.manualSeed)
    random.seed(opt.manualSeed)
    torch.manual_seed(opt.manualSeed)
    np.random.seed(opt.manualSeed)
    Loss_curve_train = []
    Loss_curve_val = []
    Loss_curve_val_amass = []

    # meters to record stats on learning
    train_loss = AverageValueMeter()
    val_loss = AverageValueMeter()
    val_loss_amass = AverageValueMeter()
    tmp_val_loss = AverageValueMeter()

    # ===================CREATE DATASET================================= #
    dataset = FaustProjectionsPart2PartDataset(train=True, num_input_channels=opt.num_input_channels,
                                      train_size=opt.p2p_faust_train_size, test_size = opt.p2p_faust_test_size)

    #dataset = AmassProjectionsDataset(split='train', num_input_channels=opt.num_input_channels, filtering=opt.filtering,
    #                                  mask_penalty=opt.mask_xyz_penalty, use_same_subject=opt.use_same_subject,
    #                                  train_size=opt.amass_train_size, validation_size=opt.amass_validation_size)

    # dataset = FaustProjectionsDataset(train=True, num_input_channels=opt.num_input_channels,
    #                                   train_size=opt.faust_train_size, test_size = opt.faust_test_size, mask_penalty=opt.mask_xyz_penalty)

    # dataset = DfaustProjectionsDataset(train=True, num_input_channels=opt.num_input_channels,
    #                                    train_size=opt.dynamic_faust_train_size, test_size =opt.dynamic_faust_test_size, mask_penalty=opt.mask_xyz_penalty)

    dataloader = torch.utils.data.DataLoader(dataset, batch_size=opt.batchSize, shuffle=True,
                                             num_workers=int(opt.workers), pin_memory=True)
    # OH: pin_memory=True used to increase the performance when transferring the fetched data from CPU to GPU
    dataset_test = FaustProjectionsDataset(train=False, num_input_channels=opt.num_input_channels,
                                           train_size=opt.faust_train_size, mask_penalty=opt.mask_xyz_penalty)

    dataloader_test = torch.utils.data.DataLoader(dataset_test, batch_size=opt.batchSize, shuffle=True,
                                                  num_workers=int(opt.workers), pin_memory=True)
    dataset_test_amass = AmassProjectionsDataset(split='validation', num_input_channels=opt.num_input_channels,
                                                 filtering=opt.filtering,
                                                 mask_penalty=opt.mask_xyz_penalty,
                                                 use_same_subject=opt.use_same_subject,
                                                 train_size=opt.amass_train_size,
                                                 validation_size=opt.amass_validation_size)
    dataloader_test_amass = torch.utils.data.DataLoader(dataset_test_amass, batch_size=opt.batchSize, shuffle=True,
                                                        num_workers=int(opt.workers), pin_memory=True)

    len_dataset = len(dataset)
    len_dataset_test = len(dataset_test)
    len_dataset_test_amass = len(dataset_test_amass)

    # get dataset triangulations
    if opt.normal_loss_slope > 0:
        triv_tup = dataset_test.triangulation(use_torch=True)
    else:
        triv_tup = None

    # ===================CREATE network================================= #
    network = CompletionNet(num_input_channels=opt.num_input_channels, num_output_channels=opt.num_output_channels,
                            centering=opt.centering)
    network.cuda()  # put network on GPU
    network.apply(weights_init)  # initialization of the weight
    old_epoch = 0
    try:
        if opt.model_dir != '':
            model_path = os.path.join(os.getcwd(), "log", opt.model_dir, opt.model_file)
            print(model_path)
            network.load_state_dict(torch.load(model_path))
            print(" Previous weight loaded ")
            log_path = os.path.join(os.getcwd(), "log", opt.model_dir, "log.txt")
            print(log_path)
            old_epoch = read_lr(log_path)
    except:
        print('Saved weights mismatch in input size - Retraining')
    # ========================================================== #

    # ===================CREATE optimizer================================= #
    lrate = 0.001 / max(1, (old_epoch // 100) * 2)  # learning rate
    optimizer = optim.Adam(network.parameters(), lr=lrate)

    with open(logname, 'a') as f:  # open and append
        f.write(str(network) + '\n')
    # ========================================================== #

    # =============start of the learning loop ======================================== #
    for epoch in range(opt.nepoch):
        part = None
        template = None
        pointsReconstructed = None
        gt = None
        mask = None

        if (epoch % 100) == 99:
            lrate = lrate / 2.0
            optimizer = optim.Adam(network.parameters(), lr=lrate)

        # TRAIN MODE
        train_loss.reset()
        network.train()
        for i, data in enumerate(dataloader, 0):

            optimizer.zero_grad()

            # Bring in data to GPU
            # template = Full shape, uses part to achieve the gt
            # part = gt[mask_full] - A part of the gt, used to direct template where to advance
            # gt = The ground truth - Our optimization target
            # mask = A vector of ones and mask_penalty values, where mask[:,:,[i]] == mask_penalty for vertices in part

            if dataset.__class__.__name__ == 'FaustProjectionsDataset':
                part, template, gt, subject_id_full, mask_loss = data
            else:
                template, part, gt, subject_id_full, subject_id_part, pose_id_full, pose_id_part, mask_id, mask_loss, _ = data

            part = part.transpose(2, 1).contiguous().cuda().float()
            template = template.transpose(2, 1).contiguous().cuda().float()
            gt = gt.transpose(2, 1).contiguous().cuda().float()
            mask_loss = torch.unsqueeze(mask_loss, 2).transpose(2, 1).contiguous().cuda().float()  # [B x 1 x N]
            # Forward pass
            gt_rec, _, part_centering = network(part, template)
            # Center gt
            gt[:, :3, :] -= part_centering

            loss = compute_loss(gt, gt_rec, template, mask_loss, triv_tup, opt)

            train_loss.update(loss.item())
            loss.backward()
            optimizer.step()  # gradient update

            # VIZUALIZE
            if opt.use_visdom and i % 100 == 0:
                vis.scatter(X=part[0, :3, :].transpose(1, 0).contiguous().data.cpu(), win='Train_Part',
                            opts=dict(title="Train_Part", markersize=2, ), )
                vis.scatter(X=template[0, :3, :].transpose(1, 0).contiguous().data.cpu(), win='Train_Template',
                            opts=dict(title="Train_Template", markersize=2, ), )
                vis.scatter(X=gt_rec[0, :3, :].transpose(1, 0).contiguous().data.cpu(), win='Train_output',
                            opts=dict(title="Train_output", markersize=2, ), )
                vis.scatter(X=gt[0, :3, :].transpose(1, 0).contiguous().data.cpu(), win='Train_Ground_Truth',
                            opts=dict(title="Train_Ground_Truth", markersize=2, ), )

            print('[%d: %d/%d] train loss:  %f' % (epoch, i, len_dataset / opt.batchSize, loss.item()))

        # Validation Faust
        with torch.no_grad():
            network.eval()
            val_loss.reset()
            for i, data in enumerate(dataloader_test, 0):
                part, template, gt, subject_id_full, mask_loss = data
                # OH: place on GPU
                part = part.transpose(2, 1).contiguous().cuda(non_blocking=True).float()
                template = template.transpose(2, 1).contiguous().cuda(non_blocking=True).float()
                gt = gt.transpose(2, 1).contiguous().cuda(non_blocking=True).float()
                mask_loss = torch.unsqueeze(mask_loss, 2).transpose(2, 1).contiguous().cuda().float()
                # Forward pass
                gt_rec, _, part_centering = network(part, template)
                gt[:, :3, :] -= part_centering

                # In Faust validation we don't use the mask right now (Faust dataloader doesn't return the mask yet)
                # TODO: return the indices of the part of the part within Faust dataloader
                loss = compute_loss(gt, gt_rec, template, mask_loss, triv_tup, opt)
                val_loss.update(loss.item())

                # VIZUALIZE
                if opt.use_visdom and i % 100 == 0:
                    vis.scatter(X=part[0, :3, :].transpose(1, 0).contiguous().data.cpu(), win='Test_Part',
                                opts=dict(title="Test_Part", markersize=2, ), )
                    vis.scatter(X=template[0, :3, :].transpose(1, 0).contiguous().data.cpu(), win='Test_Template',
                                opts=dict(title="Test_Template", markersize=2, ), )
                    vis.scatter(X=gt_rec[0].transpose(1, 0).contiguous().data.cpu(), win='Test_output',
                                opts=dict(title="Test_output", markersize=2, ), )
                    vis.scatter(X=gt[0, :3, :].transpose(1, 0).contiguous().data.cpu(), win='Test_Ground_Truth',
                                opts=dict(title="Test_Ground_Truth", markersize=2, ), )

                print('[%d: %d/%d] test loss:  %f' % (epoch, i, len_dataset_test / opt.batchSize, loss.item()))

        # Validation AMASS
        with torch.no_grad():
            network.eval()
            val_loss_amass.reset()
            for i, data in enumerate(dataloader_test_amass, 0):
                template, part, gt, subject_id_full, subject_id_part, pose_id_full, pose_id_part, mask_id, mask_loss, _ = data
                # OH: place on GPU
                part = part.transpose(2, 1).contiguous().cuda(non_blocking=True).float()
                template = template.transpose(2, 1).contiguous().cuda(non_blocking=True).float()
                gt = gt.transpose(2, 1).contiguous().cuda(non_blocking=True).float()
                mask_loss = torch.unsqueeze(mask_loss, 2).transpose(2, 1).contiguous().cuda().float()  # [B x 1 x N]

                # Forward pass
                gt_rec, _, part_centering = network(part, template)
                gt[:, :3, :] -= part_centering
                loss = compute_loss(gt, gt_rec, template, None, triv_tup, opt)

                val_loss_amass.update(loss.item())

                # VIZUALIZE
                if opt.use_visdom and i % 100 == 0:
                    vis.scatter(X=part[0, :3, :].transpose(1, 0).contiguous().data.cpu(), win='Test_Amass_Part',
                                opts=dict(title="Test_Amass_Part", markersize=2, ), )
                    vis.scatter(X=template[0, :3, :].transpose(1, 0).contiguous().data.cpu(), win='Test_Amass_Template',
                                opts=dict(title="Test_Amass_Template", markersize=2, ), )
                    vis.scatter(X=gt_rec[0, :3, :].transpose(1, 0).contiguous().data.cpu(),
                                win='Test_Amass_output',

                                opts=dict(title="Test_Amass_output", markersize=2, ), )
                    vis.scatter(X=gt[0, :3, :].transpose(1, 0).contiguous().data.cpu(), win='Test_Amass_Ground_Truth',
                                opts=dict(title="Test_Amass_Ground_Truth", markersize=2, ), )

                print(
                    '[%d: %d/%d] test loss:  %f' % (epoch, i, len_dataset_test_amass / opt.batchSize, loss.item()))

        # UPDATE CURVES
        Loss_curve_train.append(train_loss.avg)
        Loss_curve_val.append(val_loss.avg)
        Loss_curve_val_amass.append(val_loss_amass.avg)

        if opt.use_visdom:
            vis.line(X=np.column_stack((np.arange(len(Loss_curve_train)), np.arange(len(Loss_curve_val)))),
                     Y=np.column_stack((np.array(Loss_curve_train), np.array(Loss_curve_val))),
                     win='Faust loss',
                     opts=dict(title="Faust loss", legend=["Train loss", "Faust Validation loss", ]))
            vis.line(X=np.column_stack((np.arange(len(Loss_curve_train)), np.arange(len(Loss_curve_val)))),
                     Y=np.log(np.column_stack((np.array(Loss_curve_train), np.array(Loss_curve_val)))),
                     win='"Faust log loss',
                     opts=dict(title="Faust log loss", legend=["Train loss", "Faust Validation loss", ]))

            vis.line(X=np.column_stack((np.arange(len(Loss_curve_train)), np.arange(len(Loss_curve_val_amass)))),
                     Y=np.column_stack((np.array(Loss_curve_train), np.array(Loss_curve_val_amass))),
                     win='AMASS loss',
                     opts=dict(title="AMASS loss", legend=["Train loss", "Validation loss", "Validation loss amass"]))
            vis.line(X=np.column_stack((np.arange(len(Loss_curve_train)), np.arange(len(Loss_curve_val_amass)))),
                     Y=np.log(np.column_stack((np.array(Loss_curve_train), np.array(Loss_curve_val_amass)))),
                     win='AMASS log loss',
                     opts=dict(title="AMASS log loss", legend=["Train loss", "Faust Validation loss", ]))

        # dump stats in log file
        log_table = {
            "val_loss": val_loss.avg,
            "train_loss": train_loss.avg,
            "val_loss_amass": val_loss_amass.avg,
            "epoch": epoch,
            "lr": lrate,
            "env": opt.save_path,
        }
        print(log_table)
        with open(logname, 'a') as f:  # open and append
            f.write('json_stats: ' + json.dumps(log_table) + '\n')
            f.write("EPOCH NUMBER: " + str(epoch + old_epoch) + "\n")

        # save latest network
        torch.save(network.state_dict(), '%s/network_last.pth' % (dir_name))


def compute_loss(gt, gt_rec, template, mask_loss, f_tup, opt):
    gt_rec_xyz = gt_rec[:, :3, :]
    gt_xyz = gt[:, :3, :]
    template_xyz = template[:, :3, :]

    # Compute XYZ Loss
    multiplying_factor = 1
    if opt.mask_xyz_penalty and mask_loss is not None:
        multiplying_factor *= mask_loss
    if opt.distant_vertex_loss_slope > 0:
        distant_vertex_penalty = torch.norm(gt_xyz - template_xyz, dim=1, keepdim=True)  # Vector
        distant_vertex_penalty /= torch.mean(distant_vertex_penalty, dim=2, keepdim=True)
        distant_vertex_penalty = torch.max(distant_vertex_penalty, torch.ones((1, 1, 1), device='cuda'))
        distant_vertex_penalty[distant_vertex_penalty > 1] *= opt.distant_vertex_loss_slope
        # distant_vertex_loss = torch.mean(distant_vertex_penalty * ((gt_rec_xyz - gt_xyz) ** 2))
        # print(f'Distant Vertex Loss {distant_vertex_loss:4f}')
        multiplying_factor *= distant_vertex_penalty
    loss = torch.mean(multiplying_factor * ((gt_rec_xyz - gt_xyz) ** 2))
    # loss = torch.tensor(0).float().cuda()

    # Compute Normal Loss
    if opt.normal_loss_slope > 0:
        gt_rec_n = calc_vnrmls_batch(gt_rec_xyz, f_tup)
        if gt.shape[1] > 3:  # Has normals
            gt_n = gt[:, 3:6, :]
        else:
            gt_n = calc_vnrmls_batch(gt_xyz, f_tup)

        multiplying_factor = 1
        if opt.use_mask_normal_penalty and mask_loss is not None:
            multiplying_factor *= mask_loss
        if opt.use_mask_normal_distant_vertex_penalty:
            multiplying_factor *= distant_vertex_penalty

        normal_loss = opt.normal_loss_slope * torch.mean(multiplying_factor * ((gt_rec_n - gt_n) ** 2))
        # print(f'Vertex Normal Loss {normal_loss:4f}')
        loss += normal_loss

    # Compute Euclidean Distance Loss
    if opt.euclid_dist_loss_slope > 0:
        euclid_dist_loss = opt.euclid_dist_loss_slope * torch.mean(
            (calc_euclidean_dist_matrix(gt_rec_xyz) - calc_euclidean_dist_matrix(gt_xyz)) ** 2)
        # print(f'Euclid Distances Loss {euclid_dist_loss:4f}')
        loss += euclid_dist_loss

    return loss


if __name__ == '__main__': main()