import numpy as np
from scores.utils import get_score
import torch
import math


class Predictor:
    def __init__(self, args, net):
        self.score_function = get_score(args)

        self.net = net
        self.threshold = None
        self.alpha = args.alpha
        self.device = next(net.parameters()).device
        self.args = args

    def calibrate(self, cal_loader, alpha=None):
        """ Input calibration dataloader.
            Compute scores for all the calibration data and take the (1 - alpha) quantile."""
        self.net.eval()
        with torch.no_grad():
            if alpha is None:
                alpha = self.alpha
            cal_score = torch.tensor([], device=self.device)
            for data, target in cal_loader:
                data = data.to(self.device)
                target = target.to(self.device)

                logits = self.net(data)

                prob = torch.softmax(logits, dim=1)
                batch_score = self.score_function.compute_target_score(prob, target)

                cal_score = torch.cat((cal_score, batch_score), 0)
            N = cal_score.shape[0]
            threshold = torch.quantile(cal_score, math.ceil((1 - alpha) * (N + 1)) / N, dim=0)
            self.threshold = threshold
            return threshold

    def calibrate_batch_logit(self, logits, target, alpha):
        """Design for conformal training, which needs to compute threshold in every batch"""
        prob = torch.softmax(logits, dim=-1)
        batch_score = self.score_function.compute_target_score(prob, target)
        N = target.shape[0]
        return torch.quantile(batch_score, math.ceil((1 - alpha) * (N + 1)) / N, dim=0)

    def evaluate(self, test_loader):
        """Must be called after calibration.
        Output a dictionary containing Top1 Accuracy, Coverage and Average Prediction Set Size."""
        self.net.eval()
        if self.threshold is not None:
            num_classes = test_loader.dataset.num_classes
            with torch.no_grad():
                total_accuracy = 0
                total_coverage = 0
                total_prediction_set_size = 0
                class_coverage = [0 for i in range(num_classes)]
                class_size = [0 for i in range(num_classes)]
                total_samples = 0

                for data, target in test_loader:
                    data, target = data.to(self.device), target.to(self.device)
                    batch_size = target.shape[0]
                    total_samples += batch_size

                    logit = self.net(data)
                    prob = torch.softmax(logit, dim=-1)
                    prediction = torch.argmax(prob, dim=-1)
                    total_accuracy += (prediction == target).sum().item()

                    batch_score = self.score_function(prob)
                    prediction_set = (batch_score <= self.threshold).to(torch.int)

                    target_prediction_set = prediction_set[torch.arange(batch_size), target]
                    total_coverage += target_prediction_set.sum().item()

                    total_prediction_set_size += prediction_set.sum().item()

                    for i in range(prediction_set.shape[0]):
                        class_coverage[target[i]] += 1
                        class_size[target[i]] += 1


                accuracy = total_accuracy / total_samples
                coverage = total_coverage / total_samples
                avg_set_size = total_prediction_set_size / total_samples
                class_coverage_gap = np.array(class_coverage) / np.array(class_size)
                class_coverage_gap = np.sum(np.abs(class_coverage_gap - (1 - self.alpha))) / num_classes
                result_dict = {
                    f"{self.args.score}_Top1Accuracy": accuracy,
                    f"{self.args.score}_AverageSetSize": avg_set_size,
                    f"{self.args.score}_Coverage": coverage,
                    f"{self.args.score}_class_coverage_gap": class_coverage_gap,
                }
        else:
            total_samples = 0
            total_accuracy = 0
            with torch.no_grad():
                for data, target in test_loader:
                    data, target = data.to(self.device), target.to(self.device)
                    batch_size = target.shape[0]
                    total_samples += batch_size

                    logit = self.net(data)
                    prob = torch.softmax(logit, dim=-1)
                    prediction = torch.argmax(prob, dim=-1)
                    total_accuracy += (prediction == target).sum().item()

                accuracy = total_accuracy / total_samples
                result_dict = {
                    f"{self.args.score}_Top1Accuracy": accuracy,
                }

        return result_dict

