# -*- coding: utf-8 -*-
"""
Created on Tue Jul 28 09:51:12 2020

@author: huijianpzh
"""
import os
import numpy as np
import matplotlib.pyplot as plt
from tqdm import tqdm

import torch
import torch.optim as optim 
import torch.nn.functional as F

from skimage import io

import warnings
warnings.filterwarnings("ignore")

def _sample_inpaint(sample,cuda=False,device=None):
    
    input_ = sample["input_"]
    mask = sample["mask"]
    image = sample["image"]
    
    if cuda:
        input_ = input_.to(device)
        mask = mask.to(device)
        image = image.to(device)
    
    return input_,mask,image

def _sample_segmentation(sample,cuda=False,device=None):
    
    image = sample["image"]
    label = sample["label"]
    
    if cuda:
        image= image.to(device)
        label = label.to(device)
    
    return image,label

def _save_model(net,model_name="seg",model_path = "../checkpoint",cuda=False,device=None):
    if cuda:
        net = net.cpu()
    torch.save(net,model_path+"/"+model_name+".pkl")
    return

def _restore_model(model_name="seg",model_path="../checkpoint",cuda=False,device=None):
    net = torch.restore(model_path+"/"+model_name+".pkl")
    if cuda:
        net = net.to(device)
    return net

def _visualize_inpaint(dataloader,
                       net,coach,
                       stats,
                       prefix,
                       cuda=False,device=None,
                       pic_dir = "../temp"):
    net.eval()
    coach.eval()
    
    mean_rgb = stats[0,:,0] # [3]
    std_rgb = stats[1,:,0] # [3]
    
    for batch_idx,sample in tqdm(enumerate(dataloader)):
        
        if (batch_idx+1) == 12:
            break
        
        # input_ is actually the image, and the "image" is the target
        input_,mask,image = _sample_inpaint(sample,cuda=cuda,device=device)
        
        coach_mask = coach(input_,alpha = 100)
        inpaint_pred = net(input_*coach_mask)
        
        
        bs = input_.size(0)
        # to numpy 
        # [bs,c,h,w] -> [bs,h,w,c]
        input_ = input_.cpu().numpy()
        input_ = input_.transpose(0,2,3,1)
        # [bs,c,h,w] -> [bs,h,w,c]
        inpaint_pred = inpaint_pred.detach().cpu().numpy()
        inpaint_pred = inpaint_pred.transpose(0,2,3,1)
        # [bs,1,h,w] -> [bs,h,w,1]
        coach_mask = coach_mask.detach().cpu().numpy()
        coach_mask = coach_mask.transpose(0,2,3,1)
        
        for b_ in range(bs):
            input_b_ = input_[b_] # [h,w,c]
            input_b_ = input_b_ + mean_rgb
            input_b_ = input_b_.clip(0,255)
            input_b_ = input_b_.astyp(np.uint8)
            
            inpaint_b_ = inpaint_pred[b_] # [h,w,c]
            inpaint_b_[:,:,0] *= 3*std_rgb[:,:,0]
            inpaint_b_[:,:,1] *= 3*std_rgb[:,:,1]
            inpaint_b_[:,:,2] *= 3*std_rgb[:,:,2]
            inpaint_b_ = inpaint_b_ + mean_rgb
            inpaint_b_ = inpaint_b_.clip(0,255)
            inpaint_b_ = inpaint_b_.astype(np.uint8)
            
            coach_mask_b_ = coach_mask[b_] # [h,w,1]
            coach_mask_b_ = coach_mask_b_[:,:,0] # [h,w]
            coach_mask_b_ = coach_mask_b_.clip(0,1)
            
            io.imsave(fname=pic_dir + "/" + prefix + "input_image.jpg",
                      arr=input_b_)
            io.imsave(fname=pic_dir + "/" + prefix + "inpainted_image.jpg",
                      arr=inpaint_b_)
            io.imsave(fname=pic_dir + "/" + prefix + "coach_mask.jpg",
                      arr=coach_mask_b_)
    
    net.train()
    coach.train()
    return

def _rec_loss(pred,target,mask):
    mse_loss = (pred-target)**2
    mse_loss = -1*F.threshold(-1*mse_loss,-2,-2)
    loss = torch.sum(mse_loss*(1-mask))/(torch.sum(1-mask))
    return loss

def validate4inpaint(val_dataloader,coach,net,
                     cuda=False,device=None):
    net.eval()
    coach.eval()
    
    for batch_idx,sample in enumerate(val_dataloader):
        # get the batch data
        input_,_,image = _sample_inpaint(sample.cuda,device)
        
        loss_rec_list = []
        loss_con_list = []
        average_loss_rec = 0
        average_loss_con = 0
        with torch.no_grad():
            mask,mu,logvar = coach(input_,alpha=100)
            """
            # first loss format
            # ---begin---
            inpaint_pred_1 = net(input_*mask)
            mse_loss = (inpaint_pred_1-image)**2
            mse_loss = -1*F.threshold(-1*mse_loss,-2,-2)
            loss_rec = torch.sum(mse_loss*(1-mask))/(torch.sum(1-mask))
            # loss_con
            inpaint_pred_2 = net(input_*(1-mask))
            mse_loss = (inpaint_pred_2-image)**2
            mse_loss = -1*F.threshold(-1*mse_loss,-2,-2)
            loss_con = torch.sum(mse_loss*mask)/torch.sum(mask)
            # ---end---
            """
            
            # second loss format
            # ---begin---
            inpaint_pred = net(input_*mask)
            mse_loss = (inpaint_pred-image)**2
            mse_loss = -1*F.threshold(-1*mse_loss,-2,-2)
            loss_rec = torch.sum(mse_loss*(1-mask))/(torch.sum(1-mask))
            loss_con = torch.sum(mse_loss*mask)/torch.sum(mask)
            # --end---
            
            loss_rec_val = loss_rec.detach().cpu().numpy()[0]
            loss_con_val = loss_con.detach().cpu().numpy()[0]
            
            loss_rec_list.append(loss_rec_val)
            loss_con_list.append(loss_con_val)
            
            average_loss_rec = loss_rec_val + average_loss_rec
            average_loss_con = loss_con_val + average_loss_con
            
    average_loss_rec /= len(val_dataloader)
    average_loss_con /= len(val_dataloader)  
      
    net.train()
    coach.train()
    
    return average_loss_rec,average_loss_con,loss_rec_list,loss_con_list

def train_stepbystep(train_dataloader,
                     net,coach,
                     net_optimizer,coach_optimizer,
                     cuda=False,device=None,
                     rec_weight=0.99
                     ):
    
    for batch_idx,sample in tqdm(enumerate(train_dataloader)):
        # get batch data
        input_,_,image = _sample_inpaint(sample,cuda,device)
        
        # ---train the coach--
        coach.train()
        net.eval()
        
        coach_optimizer.zero_grad()
        
        mask,mu,logvar = coach(input_,alpha=1)
        inpaint_pred = net(input_*mask)
        # loss_rec
        mse_loss = (inpaint_pred-image)**2
        mse_loss = -1*F.threshold(-1*mse_loss,-2,-2)
        # I add 2 .detach() here, for the mask just provide a num
        loss_rec = torch.sum(mse_loss*(1-mask.detach()))/(torch.sum(1-mask.detach()))

        # loss_kld
        mu = mu.mean(dim=2).mean(dim=2)
        logvar = logvar.mean(dim=2).mean(dim=2)
        loss_kld = -0.5 * torch.sum(1 + logvar - mu.pow(2) - logvar.exp())
        
        loss = 1-loss_rec + 1e-6*loss_kld
        
        loss.backward()
        coach_optimizer.step()
        
        
        # ---train the net---
        coach.eval()
        net.train()
        
        net_optimizer.zero_grad()
        mask,_,__ = coach(input_,alpha=100)
        mask = mask.detach()
        
        """
        # first loss format
        # ---begin---
        # loss_rec
        inpaint_pred_1 = net(input_*mask)
        mse_loss = (inpaint_pred_1-image)**2
        mse_loss = -1*F.threshold(-1*mse_loss,-2,-2)
        loss_rec = torch.sum(mse_loss*(1-mask))/(torch.sum(1-mask))
        # loss_con
        inpaint_pred_2 = net(input_*(1-mask))
        mse_loss = (inpaint_pred_2-image)**2
        mse_loss = -1*F.threshold(-1*mse_loss,-2,-2)
        loss_con = torch.sum(mse_loss*mask)/torch.sum(mask)
        # ---end---
        """
        
        # second loss format
        # ---begin---
        inpaint_pred = net(input_*mask)
        mse_loss = (inpaint_pred-image)**2
        mse_loss = -1*F.threshold(-1*mse_loss,-2,-2)
        loss_rec = torch.sum(mse_loss*(1-mask))/(torch.sum(1-mask))
        loss_con = torch.sum(mse_loss*mask)/torch.sum(mask)
        # ---end--
        
        loss = loss_con*rec_weight + loss_rec*(1-rec_weight)
        loss.backward()
        net_optimizer.step()
        
    return

# --- copied from the official codes --- modified

def train_context_inpainting(train_dataloader,net,net_optimizer,
                             cuda = False,device=None,
                             rec_weight = 0.99,
                             coach=None,use_coach_masks=False):
    net.train()
    if coach is not None:
        coach.eval()
    
    for batch_idx,sample in enumerate(train_dataloader,0):
        net_optimizer.zero_grad()
        inputs_,masks,targets = _sample_inpaint(sample,cuda)
        
        if coach is not None:
            masks,_,__ = coach.forward(inputs_,alpha=100,use_coach = use_coach_masks)
        
        outputs_1 = net(inputs_*masks)
        mse_loss = (outputs_1 - targets)**2
        mse_loss = -1*F.threshold(-1*mse_loss,-2,-2)
        loss_rec = torch.sum(mse_loss*(1-masks))/(torch.sum(1-masks))
        
        if coach is not None:
            loss_con = torch.sum(mse_loss*masks)/torch.sum(masks)
        else:
            outputs_2 = net(inputs_*(1-masks))
            mse_loss = (outputs_2 - targets)**2
            mse_loss = -1*F.threshold(-1*mse_loss,-2,-2)
            loss_con = torch.sum(mse_loss*masks)/torch.sum(masks)
        
        total_loss = rec_weight*loss_rec + (1-rec_weight)*loss_con
        total_loss.backward()
        net_optimizer.step()
        
    return

def train_coach(train_dataloader,net,coach,coach_optimizer,
                cuda=False,device=None):
    
    coach.train()
    net.eval()
    
    for batch_idx,sample in enumerate(train_dataloader,0):
        coach_optimizer.zero_grad()
        
        inputs_,masks,targets = _sample_inpaint(sample,cuda)
        masks,mu,logvar = coach.forward(inputs_,alpha=1)
        
        # In the official codes, there is a .detach() here.
        outputs = net(inputs_*masks)
        mse_loss = (outputs-targets)**2
        mse_loss = -1*F.threshold(-1*mse_loss, -2, -2)
    
        loss_rec = torch.sum(mse_loss*(1-masks))/(torch.sum(1-masks))
        
        mu = mu.mean(dim=2).mean(dim=2)
        logvar = logvar.mean(dim=2).mean(dim=2)
        
        loss_kld = 0
        try:
            loss_kld = -0.5 * torch.sum(1 + logvar - mu.pow(2) - logvar.exp())
        except:
            loss_kld = 0
        
        total_loss = 1-loss_rec + 1e-6*loss_kld
        
        total_loss.backward()
        coach_optimizer.step()
        
    return
