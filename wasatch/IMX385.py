import numpy as np
import logging

log = logging.getLogger(__name__)

class IMX385:
    """ 
    The IMX385-LQR-C sensor has a Bayer filter such that every four pixels (two 
    horiz, two vert) contain one Red, one Blue and two Green:

    R G R G R G ...
    G B G B G B ...
    R G R G R G ...

    Assuming the 'reported intensity' of any vertically binned column represents
    the sum of an equal number of 'green' and 'color' (red or blue) pixels, then:
        
        reported_intensity = green_factor(true_intensity) + color_factor(true_intensity)
        reported_intensity = true_intensity(green_factor + color_factor)
        true_intensity = reported_intensity / (green_factor + color_factor)
    """
    
    def __init__(self):
        
        # from Spectral Sensitivity Characteristics graph in IMX385-LQR-C datasheet (p22)
        self.factors = {
            "red":   np.asarray([ 0.0800, 0.0797, 0.0782, 0.0767, 0.0751, 0.0736, 0.0721, 0.0705, 0.0692, 0.0679, 0.0666, 0.0653, 0.0640, 0.0627, 0.0614, 0.0601, 0.0588, 0.0576, 0.0563, 0.0550, 0.0537, 0.0524, 0.0511, 0.0499, 0.0494, 0.0489, 0.0483, 0.0478, 0.0473, 0.0467, 0.0462, 0.0457, 0.0451, 0.0446, 0.0441, 0.0435, 0.0430, 0.0425, 0.0419, 0.0414, 0.0409, 0.0403, 0.0398, 0.0392, 0.0385, 0.0379, 0.0373, 0.0367, 0.0361, 0.0355, 0.0349, 0.0343, 0.0336, 0.0330, 0.0324, 0.0318, 0.0312, 0.0306, 0.0300, 0.0305, 0.0309, 0.0313, 0.0318, 0.0322, 0.0326, 0.0331, 0.0335, 0.0340, 0.0344, 0.0348, 0.0353, 0.0357, 0.0361, 0.0366, 0.0370, 0.0375, 0.0379, 0.0383, 0.0388, 0.0392, 0.0396, 0.0401, 0.0408, 0.0415, 0.0422, 0.0428, 0.0435, 0.0442, 0.0449, 0.0456, 0.0462, 0.0469, 0.0476, 0.0483, 0.0490, 0.0497, 0.0508, 0.0523, 0.0538, 0.0553, 0.0569, 0.0584, 0.0599, 0.0615, 0.0630, 0.0645, 0.0661, 0.0676, 0.0691, 0.0708, 0.0727, 0.0746, 0.0765, 0.0784, 0.0802, 0.0821, 0.0840, 0.0859, 0.0878, 0.0897, 0.0900, 0.0900, 0.0900, 0.0900, 0.0900, 0.0900, 0.0897, 0.0886, 0.0874, 0.0863, 0.0852, 0.0841, 0.0830, 0.0819, 0.0808, 0.0796, 0.0784, 0.0772, 0.0759, 0.0747, 0.0735, 0.0723, 0.0711, 0.0700, 0.0700, 0.0700, 0.0700, 0.0700, 0.0700, 0.0700, 0.0700, 0.0700, 0.0712, 0.0724, 0.0737, 0.0749, 0.0761, 0.0773, 0.0786, 0.0798, 0.0872, 0.0959, 0.1047, 0.1134, 0.1221, 0.1325, 0.1570, 0.1815, 0.2060, 0.2305, 0.2550, 0.2795, 0.3098, 0.3445, 0.3792, 0.4139, 0.4486, 0.4867, 0.5266, 0.5665, 0.6050, 0.6417, 0.6784, 0.7141, 0.7435, 0.7729, 0.8024, 0.8317, 0.8593, 0.8869, 0.9145, 0.9298, 0.9420, 0.9543, 0.9632, 0.9694, 0.9755, 0.9816, 0.9877, 0.9909, 0.9922, 0.9936, 0.9949, 0.9963, 0.9977, 0.9990, 0.9994, 0.9974, 0.9953, 0.9933, 0.9912, 0.9894, 0.9879, 0.9863, 0.9848, 0.9833, 0.9817, 0.9802, 0.9800, 0.9800, 0.9800, 0.9800, 0.9800, 0.9800, 0.9800, 0.9800, 0.9800, 0.9790, 0.9779, 0.9768, 0.9757, 0.9746, 0.9735, 0.9723, 0.9712, 0.9701, 0.9700, 0.9700, 0.9700, 0.9700, 0.9700, 0.9700, 0.9700, 0.9700, 0.9700, 0.9700, 0.9700, 0.9688, 0.9664, 0.9639, 0.9615, 0.9590, 0.9566, 0.9541, 0.9517, 0.9487, 0.9447, 0.9406, 0.9365, 0.9324, 0.9283, 0.9242, 0.9202, 0.9136, 0.9069, 0.9002, 0.8935, 0.8869, 0.8802, 0.8735, 0.8668, 0.8601, 0.8552, 0.8503, 0.8454, 0.8405, 0.8356, 0.8307, 0.8258, 0.8209, 0.8173, 0.8139, 0.8106, 0.8072, 0.8039, 0.8006, 0.7972, 0.7939, 0.7905, 0.7887, 0.7872, 0.7857, 0.7841, 0.7826, 0.7811, 0.7804, 0.7818, 0.7831, 0.7845, 0.7859, 0.7872, 0.7886, 0.7899, 0.7926, 0.7953, 0.7981, 0.8008, 0.8035, 0.8062, 0.8089, 0.8117, 0.8144, 0.8171, 0.8198, 0.8226, 0.8253, 0.8280, 0.8300, 0.8300, 0.8300, 0.8300, 0.8297, 0.8282, 0.8267, 0.8251, 0.8236, 0.8221, 0.8205, 0.8177, 0.8142, 0.8107, 0.8072, 0.8037, 0.8002, 0.7969, 0.7935, 0.7902, 0.7868, 0.7835, 0.7802, 0.7768, 0.7735, 0.7701, 0.7687, 0.7673, 0.7660, 0.7646, 0.7633, 0.7619, 0.7605, 0.7588, 0.7567, 0.7547, 0.7526, 0.7506, 0.7485, 0.7465, 0.7445, 0.7424, 0.7404, 0.7377, 0.7349, 0.7320, 0.7292, 0.7264, 0.7236, 0.7207, 0.7179, 0.7151, 0.7123, 0.7091, 0.7047, 0.7002, 0.6957, 0.6913, 0.6868, 0.6824, 0.6779, 0.6735, 0.6693, 0.6659, 0.6626, 0.6592, 0.6559, 0.6526, 0.6492, 0.6459, 0.6425, 0.6392, 0.6359, 0.6327, 0.6294, 0.6261, 0.6229, 0.6196, 0.6163, 0.6131, 0.6098, 0.6065, 0.6033, 0.6000, 0.5980, 0.5959, 0.5939, 0.5918, 0.5898, 0.5877, 0.5857, 0.5837, 0.5816, 0.5796, 0.5775, 0.5755, 0.5735, 0.5714, 0.5692, 0.5668, 0.5643, 0.5619, 0.5594, 0.5570, 0.5545, 0.5521, 0.5496, 0.5472, 0.5448, 0.5423, 0.5398, 0.5365, 0.5331, 0.5298, 0.5264, 0.5231, 0.5198, 0.5164, 0.5131, 0.5097, 0.5058, 0.5018, 0.4979, 0.4940, 0.4901, 0.4862, 0.4822, 0.4783, 0.4744, 0.4705, 0.4666, 0.4627, 0.4587, 0.4548, 0.4509, 0.4470, 0.4431, 0.4391, 0.4352, 0.4313, 0.4282, 0.4256, 0.4230, 0.4204, 0.4177, 0.4151, 0.4125, 0.4099, 0.4072, 0.4046, 0.4020, 0.3993, 0.3962, 0.3931, 0.3901, 0.3870, 0.3840, 0.3809, 0.3778, 0.3748, 0.3717, 0.3686, 0.3656, 0.3625, 0.3595, 0.3568, 0.3541, 0.3513, 0.3486, 0.3459, 0.3432, 0.3404, 0.3377, 0.3350, 0.3323, 0.3296, 0.3268, 0.3241, 0.3214, 0.3185, 0.3154, 0.3124, 0.3093, 0.3063, 0.3032, 0.3001, 0.2971, 0.2940, 0.2909, 0.2879, 0.2848, 0.2818, 0.2787, 0.2756, 0.2726, 0.2695, 0.2664, 0.2634, 0.2603, 0.2573, 0.2542, 0.2511, 0.2481, 0.2450, 0.2419, 0.2389, 0.2358, 0.2328, 0.2298, 0.2282, 0.2266, 0.2249, 0.2233, 0.2217, 0.2200, 0.2184, 0.2168, 0.2151, 0.2135, 0.2119, 0.2102, 0.2086, 0.2070, 0.2053, 0.2037, 0.2021, 0.2004, 0.1988, 0.1972, 0.1955, 0.1939, 0.1923, 0.1906, 0.1886, 0.1864, 0.1842, 0.1820, 0.1797, 0.1775, 0.1753, 0.1731, 0.1708, 0.1686, 0.1664, 0.1641, 0.1619, 0.1597, 0.1575, 0.1552, 0.1530, 0.1508, 0.1480, 0.1449, 0.1419, 0.1388, 0.1358, 0.1327, 0.1296, 0.1266, 0.1235, 0.1204, 0.1174, 0.1143, 0.1113, 0.1091, 0.1077, 0.1063, 0.1048, 0.1034, 0.1019, 0.1005, 0.0991, 0.0976, 0.0962, 0.0947, 0.0933, 0.0919, 0.0904, 0.0900, 0.0900, 0.0900, 0.0900, 0.0900, 0.0900 ]),
            "blue":  np.asarray([ 0.3900, 0.3925, 0.3949, 0.3974, 0.3998, 0.4056, 0.4118, 0.4179, 0.4240, 0.4301, 0.4363, 0.4424, 0.4485, 0.4546, 0.4608, 0.4669, 0.4730, 0.4791, 0.4856, 0.4921, 0.4985, 0.5050, 0.5115, 0.5180, 0.5245, 0.5310, 0.5374, 0.5439, 0.5504, 0.5569, 0.5634, 0.5699, 0.5746, 0.5793, 0.5840, 0.5888, 0.5935, 0.5982, 0.6029, 0.6076, 0.6123, 0.6170, 0.6217, 0.6263, 0.6309, 0.6355, 0.6401, 0.6447, 0.6493, 0.6515, 0.6532, 0.6550, 0.6567, 0.6585, 0.6602, 0.6620, 0.6637, 0.6655, 0.6672, 0.6690, 0.6700, 0.6700, 0.6700, 0.6700, 0.6700, 0.6700, 0.6700, 0.6699, 0.6686, 0.6672, 0.6658, 0.6645, 0.6631, 0.6618, 0.6604, 0.6565, 0.6516, 0.6467, 0.6418, 0.6357, 0.6289, 0.6221, 0.6153, 0.6085, 0.6016, 0.5948, 0.5872, 0.5777, 0.5682, 0.5587, 0.5491, 0.5396, 0.5301, 0.5206, 0.5099, 0.4992, 0.4885, 0.4778, 0.4670, 0.4563, 0.4455, 0.4347, 0.4238, 0.4129, 0.4020, 0.3911, 0.3802, 0.3694, 0.3586, 0.3479, 0.3372, 0.3265, 0.3158, 0.3050, 0.2959, 0.2883, 0.2806, 0.2730, 0.2653, 0.2577, 0.2500, 0.2432, 0.2364, 0.2296, 0.2228, 0.2160, 0.2092, 0.2024, 0.1971, 0.1927, 0.1882, 0.1837, 0.1793, 0.1748, 0.1704, 0.1659, 0.1615, 0.1573, 0.1532, 0.1491, 0.1450, 0.1409, 0.1368, 0.1328, 0.1287, 0.1246, 0.1205, 0.1164, 0.1123, 0.1089, 0.1062, 0.1036, 0.1010, 0.0984, 0.0957, 0.0931, 0.0905, 0.0879, 0.0852, 0.0826, 0.0800, 0.0783, 0.0765, 0.0748, 0.0730, 0.0713, 0.0695, 0.0678, 0.0660, 0.0643, 0.0625, 0.0608, 0.0597, 0.0591, 0.0586, 0.0580, 0.0575, 0.0569, 0.0563, 0.0558, 0.0552, 0.0547, 0.0541, 0.0536, 0.0530, 0.0524, 0.0519, 0.0513, 0.0508, 0.0502, 0.0495, 0.0487, 0.0479, 0.0471, 0.0462, 0.0454, 0.0446, 0.0438, 0.0430, 0.0421, 0.0413, 0.0405, 0.0400, 0.0400, 0.0400, 0.0400, 0.0400, 0.0400, 0.0400, 0.0400, 0.0400, 0.0400, 0.0400, 0.0400, 0.0400, 0.0400, 0.0400, 0.0400, 0.0400, 0.0400, 0.0400, 0.0400, 0.0400, 0.0400, 0.0400, 0.0400, 0.0400, 0.0400, 0.0400, 0.0400, 0.0408, 0.0422, 0.0436, 0.0449, 0.0463, 0.0476, 0.0490, 0.0504, 0.0517, 0.0531, 0.0544, 0.0558, 0.0572, 0.0585, 0.0599, 0.0617, 0.0635, 0.0654, 0.0672, 0.0690, 0.0709, 0.0727, 0.0745, 0.0764, 0.0782, 0.0801, 0.0819, 0.0837, 0.0856, 0.0874, 0.0892, 0.0906, 0.0915, 0.0924, 0.0934, 0.0943, 0.0953, 0.0962, 0.0972, 0.0981, 0.0990, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.1000, 0.0996, 0.0989, 0.0981, 0.0974, 0.0967, 0.0960, 0.0953, 0.0945, 0.0938, 0.0931, 0.0924, 0.0916, 0.0909, 0.0902, 0.0895, 0.0887, 0.0879, 0.0872, 0.0864, 0.0856, 0.0849, 0.0841, 0.0833, 0.0826, 0.0818, 0.0810, 0.0803, 0.0797, 0.0793, 0.0788, 0.0783, 0.0779, 0.0774, 0.0770, 0.0765, 0.0761, 0.0756, 0.0752, 0.0747, 0.0743, 0.0738, 0.0734, 0.0729, 0.0724, 0.0720, 0.0715, 0.0711, 0.0706, 0.0702, 0.0706, 0.0716, 0.0727, 0.0737, 0.0747, 0.0757, 0.0768, 0.0778, 0.0788, 0.0798, 0.0817, 0.0837, 0.0858, 0.0878, 0.0898, 0.0919, 0.0939, 0.0960, 0.0980, 0.1001, 0.1069, 0.1137, 0.1205, 0.1273, 0.1341, 0.1410, 0.1478, 0.1582, 0.1705, 0.1827, 0.1950, 0.2066, 0.2177, 0.2288, 0.2400, 0.2511, 0.2622, 0.2734, 0.2845, 0.2957, 0.3075, 0.3197, 0.3320, 0.3443, 0.3565, 0.3688, 0.3810, 0.3928, 0.4033, 0.4138, 0.4243, 0.4348, 0.4453, 0.4530, 0.4584, 0.4639, 0.4693, 0.4748, 0.4802, 0.4856, 0.4907, 0.4942, 0.4977, 0.5012, 0.5047, 0.5082, 0.5100, 0.5100, 0.5100, 0.5100, 0.5100, 0.5100, 0.5100, 0.5087, 0.5073, 0.5060, 0.5046, 0.5033, 0.5019, 0.5005, 0.4983, 0.4956, 0.4929, 0.4902, 0.4874, 0.4847, 0.4820, 0.4795, 0.4778, 0.4760, 0.4743, 0.4725, 0.4708, 0.4690, 0.4673, 0.4655, 0.4638, 0.4620, 0.4603, 0.4581, 0.4558, 0.4535, 0.4512, 0.4489, 0.4466, 0.4443, 0.4420, 0.4397, 0.4374, 0.4351, 0.4328, 0.4305, 0.4280, 0.4253, 0.4227, 0.4201, 0.4175, 0.4148, 0.4122, 0.4096, 0.4070, 0.4043, 0.4017, 0.3988, 0.3955, 0.3921, 0.3888, 0.3855, 0.3821, 0.3788, 0.3754, 0.3721, 0.3687, 0.3652, 0.3617, 0.3582, 0.3547, 0.3512, 0.3477, 0.3442, 0.3407, 0.3372, 0.3337, 0.3302, 0.3269, 0.3237, 0.3204, 0.3171, 0.3139, 0.3106, 0.3073, 0.3041, 0.3008, 0.2975, 0.2943, 0.2910, 0.2880, 0.2851, 0.2822, 0.2794, 0.2765, 0.2736, 0.2707, 0.2678, 0.2649, 0.2621, 0.2592, 0.2563, 0.2534, 0.2505, 0.2480, 0.2455, 0.2431, 0.2406, 0.2382, 0.2357, 0.2333, 0.2308, 0.2284, 0.2260, 0.2235, 0.2211, 0.2193, 0.2180, 0.2167, 0.2154, 0.2141, 0.2128, 0.2115, 0.2102, 0.2089, 0.2077, 0.2064, 0.2051, 0.2038, 0.2025, 0.2012, 0.1999, 0.1974, 0.1950, 0.1925, 0.1900, 0.1876, 0.1851, 0.1827, 0.1802, 0.1778, 0.1753, 0.1729, 0.1704, 0.1678, 0.1652, 0.1626, 0.1600, 0.1573, 0.1547, 0.1521, 0.1495, 0.1469, 0.1442, 0.1416, 0.1390, 0.1364, 0.1337, 0.1311, 0.1285, 0.1259, 0.1232, 0.1206, 0.1180, 0.1154, 0.1127, 0.1101, 0.1086, 0.1072, 0.1057, 0.1043, 0.1029, 0.1014, 0.1000, 0.0985, 0.0971, 0.0956, 0.0942, 0.0928, 0.0913, 0.0900, 0.0900, 0.0900, 0.0900, 0.0900 ]),
            "green": np.asarray([ 0.0700, 0.0691, 0.0681, 0.0672, 0.0662, 0.0653, 0.0643, 0.0634, 0.0625, 0.0615, 0.0606, 0.0597, 0.0588, 0.0579, 0.0570, 0.0562, 0.0553, 0.0544, 0.0535, 0.0527, 0.0518, 0.0509, 0.0500, 0.0500, 0.0500, 0.0500, 0.0500, 0.0500, 0.0500, 0.0500, 0.0500, 0.0500, 0.0513, 0.0526, 0.0538, 0.0551, 0.0564, 0.0577, 0.0590, 0.0603, 0.0616, 0.0629, 0.0642, 0.0654, 0.0667, 0.0680, 0.0693, 0.0706, 0.0720, 0.0734, 0.0747, 0.0761, 0.0774, 0.0788, 0.0808, 0.0878, 0.0949, 0.1019, 0.1089, 0.1159, 0.1259, 0.1401, 0.1544, 0.1687, 0.1830, 0.1998, 0.2191, 0.2384, 0.2576, 0.2769, 0.2961, 0.3196, 0.3441, 0.3685, 0.3930, 0.4175, 0.4419, 0.4651, 0.4884, 0.5117, 0.5350, 0.5583, 0.5816, 0.6049, 0.6281, 0.6461, 0.6636, 0.6810, 0.6985, 0.7160, 0.7329, 0.7472, 0.7615, 0.7758, 0.7901, 0.8021, 0.8089, 0.8157, 0.8225, 0.8293, 0.8361, 0.8429, 0.8497, 0.8553, 0.8609, 0.8665, 0.8720, 0.8776, 0.8832, 0.8888, 0.8943, 0.8999, 0.9030, 0.9061, 0.9091, 0.9122, 0.9153, 0.9183, 0.9204, 0.9214, 0.9223, 0.9233, 0.9242, 0.9251, 0.9261, 0.9270, 0.9280, 0.9289, 0.9298, 0.9300, 0.9300, 0.9300, 0.9300, 0.9300, 0.9300, 0.9300, 0.9300, 0.9300, 0.9292, 0.9284, 0.9275, 0.9266, 0.9257, 0.9249, 0.9240, 0.9231, 0.9222, 0.9214, 0.9205, 0.9187, 0.9156, 0.9126, 0.9095, 0.9064, 0.9034, 0.9003, 0.8973, 0.8942, 0.8911, 0.8870, 0.8823, 0.8776, 0.8729, 0.8682, 0.8635, 0.8588, 0.8540, 0.8493, 0.8446, 0.8399, 0.8343, 0.8288, 0.8232, 0.8176, 0.8120, 0.8065, 0.8009, 0.7953, 0.7896, 0.7801, 0.7706, 0.7610, 0.7515, 0.7420, 0.7325, 0.7230, 0.7115, 0.6993, 0.6870, 0.6748, 0.6625, 0.6502, 0.6380, 0.6249, 0.6102, 0.5955, 0.5808, 0.5661, 0.5514, 0.5367, 0.5220, 0.5078, 0.4955, 0.4833, 0.4710, 0.4588, 0.4465, 0.4343, 0.4220, 0.4098, 0.3986, 0.3875, 0.3764, 0.3652, 0.3541, 0.3430, 0.3318, 0.3207, 0.3098, 0.3044, 0.2991, 0.2937, 0.2883, 0.2830, 0.2776, 0.2723, 0.2669, 0.2615, 0.2562, 0.2508, 0.2455, 0.2401, 0.2375, 0.2349, 0.2323, 0.2297, 0.2272, 0.2246, 0.2220, 0.2194, 0.2168, 0.2143, 0.2117, 0.2091, 0.2065, 0.2039, 0.2014, 0.1993, 0.1977, 0.1962, 0.1947, 0.1932, 0.1916, 0.1901, 0.1900, 0.1900, 0.1900, 0.1900, 0.1900, 0.1907, 0.1937, 0.1968, 0.1999, 0.2029, 0.2060, 0.2091, 0.2121, 0.2152, 0.2183, 0.2218, 0.2258, 0.2299, 0.2340, 0.2381, 0.2422, 0.2462, 0.2503, 0.2544, 0.2585, 0.2626, 0.2667, 0.2707, 0.2748, 0.2789, 0.2830, 0.2871, 0.2912, 0.2953, 0.2993, 0.3034, 0.3075, 0.3117, 0.3162, 0.3206, 0.3251, 0.3296, 0.3340, 0.3385, 0.3429, 0.3474, 0.3519, 0.3565, 0.3611, 0.3657, 0.3703, 0.3749, 0.3794, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3800, 0.3805, 0.3814, 0.3823, 0.3833, 0.3842, 0.3852, 0.3861, 0.3871, 0.3880, 0.3889, 0.3899, 0.3925, 0.3953, 0.3981, 0.4010, 0.4038, 0.4066, 0.4095, 0.4123, 0.4151, 0.4179, 0.4205, 0.4224, 0.4243, 0.4262, 0.4280, 0.4299, 0.4318, 0.4337, 0.4356, 0.4375, 0.4393, 0.4408, 0.4421, 0.4434, 0.4447, 0.4460, 0.4473, 0.4486, 0.4499, 0.4512, 0.4524, 0.4537, 0.4550, 0.4563, 0.4576, 0.4589, 0.4601, 0.4610, 0.4618, 0.4627, 0.4635, 0.4643, 0.4652, 0.4660, 0.4669, 0.4677, 0.4686, 0.4694, 0.4703, 0.4711, 0.4719, 0.4728, 0.4736, 0.4745, 0.4753, 0.4762, 0.4770, 0.4779, 0.4787, 0.4796, 0.4804, 0.4812, 0.4820, 0.4828, 0.4836, 0.4845, 0.4853, 0.4861, 0.4869, 0.4877, 0.4885, 0.4894, 0.4900, 0.4900, 0.4900, 0.4900, 0.4900, 0.4900, 0.4900, 0.4900, 0.4900, 0.4893, 0.4862, 0.4831, 0.4801, 0.4770, 0.4739, 0.4709, 0.4678, 0.4647, 0.4617, 0.4589, 0.4564, 0.4540, 0.4515, 0.4491, 0.4467, 0.4442, 0.4418, 0.4393, 0.4369, 0.4344, 0.4320, 0.4295, 0.4271, 0.4246, 0.4222, 0.4197, 0.4170, 0.4144, 0.4117, 0.4090, 0.4064, 0.4037, 0.4010, 0.3984, 0.3957, 0.3930, 0.3904, 0.3877, 0.3851, 0.3824, 0.3797, 0.3771, 0.3744, 0.3717, 0.3692, 0.3669, 0.3645, 0.3622, 0.3599, 0.3575, 0.3552, 0.3529, 0.3505, 0.3482, 0.3459, 0.3435, 0.3412, 0.3389, 0.3365, 0.3342, 0.3319, 0.3294, 0.3263, 0.3233, 0.3202, 0.3171, 0.3141, 0.3110, 0.3079, 0.3049, 0.3018, 0.2987, 0.2957, 0.2926, 0.2896, 0.2865, 0.2834, 0.2804, 0.2773, 0.2742, 0.2712, 0.2681, 0.2651, 0.2620, 0.2589, 0.2559, 0.2528, 0.2497, 0.2467, 0.2436, 0.2406, 0.2383, 0.2363, 0.2342, 0.2322, 0.2302, 0.2281, 0.2261, 0.2240, 0.2220, 0.2200, 0.2179, 0.2159, 0.2138, 0.2118, 0.2098, 0.2081, 0.2065, 0.2048, 0.2031, 0.2014, 0.1998, 0.1981, 0.1964, 0.1948, 0.1931, 0.1914, 0.1898, 0.1881, 0.1864, 0.1847, 0.1831, 0.1814, 0.1796, 0.1770, 0.1744, 0.1719, 0.1693, 0.1667, 0.1641, 0.1615, 0.1590, 0.1564, 0.1538, 0.1512, 0.1486, 0.1461, 0.1435, 0.1409, 0.1386, 0.1363, 0.1341, 0.1319, 0.1296, 0.1274, 0.1252, 0.1230, 0.1207, 0.1190, 0.1175, 0.1161, 0.1146, 0.1131, 0.1117, 0.1102, 0.1087, 0.1073, 0.1058, 0.1043, 0.1028, 0.1014, 0.0999, 0.0984, 0.0970, 0.0955, 0.0940, 0.0925, 0.0911, 0.0900, 0.0900, 0.0900, 0.0900 ])
        }
        self.wavelengths = range(400, 1001, 1)

    def correct(self, spectrum, wavelengths, on_red=False):
        if spectrum is None or wavelengths is None:
            log.error("can't correct without spectrum and wavelengths")
            return spectrum

        if len(spectrum) != len(wavelengths):
            log.error("spectrum and wavelengths have different lengths")
            return spectrum

        corrected = []
        for i in range(len(spectrum)):
            reported_intensity = spectrum[i]
            wavelength = wavelengths[i]

            green_factor = np.interp(wavelength, self.wavelengths, self.factors["green"])
            color_factor = np.interp(wavelength, self.wavelengths, self.factors["red" if on_red else "blue"])

            true_intensity = reported_intensity / (green_factor + color_factor)

            # if False:
            #     if i % 100 == 0 or (i+1) % 100 == 0:
            #         print(f"correct: pixel {i:4d}, wavelength {wavelength:8.2f}nm, reported {reported_intensity:8.2f}, green {green_factor:8.2f}, color {color_factor:8.2f}, true {true_intensity:8.2f}")

            corrected.append(true_intensity)

            on_red = not on_red

        return corrected

    def bin2x2(self, spectrum):
        if spectrum is None or len(spectrum) == 0:
            return spectrum
        binned = []
        for i in range(len(spectrum)-1):
            binned.append((spectrum[i] + spectrum[i+1]) / 2.0)
        binned.append(spectrum[-1])
        return binned

# Unit Test
if __name__ == "__main__":
    # unbinned (but 3-scan averaged) cyclohexane from 633XS, cropped to horizontal ROI
    data = [ 646.86, 248.00, 646.97, 1916.33, 647.09, 290.67, 647.21, 2012.00, 647.32, 209.00, 647.44, 1998.00, 647.55, 236.67, 647.67, 2089.00, 647.78, 317.00, 647.90, 2169.67, 648.01, 339.67, 648.13, 2400.33, 648.24, 356.33, 648.36, 2562.00, 648.47, 301.33, 648.59, 2680.00, 648.70, 398.00, 648.82, 2735.33, 648.93, 370.67, 649.05, 2772.00, 649.16, 333.00, 649.28, 2729.00, 649.39, 374.33, 649.51, 2670.67, 649.62, 442.33, 649.74, 2661.00, 649.85, 293.67, 649.97, 2541.33, 650.08, 354.33, 650.19, 2800.33, 650.31, 403.33, 650.42, 3043.67, 650.54, 424.67, 650.65, 3120.00, 650.77, 433.00, 650.88, 3107.33, 650.99, 443.67, 651.11, 2881.67, 651.22, 437.00, 651.34, 2691.33, 651.45, 505.33, 651.56, 2557.33, 651.68, 366.00, 651.79, 2606.33, 651.90, 370.00, 652.02, 2498.00, 652.13, 388.00, 652.25, 2473.67, 652.36, 267.67, 652.47, 2470.33, 652.59, 315.00, 652.70, 2433.67, 652.81, 321.33, 652.93, 2475.33, 653.04, 297.00, 653.15, 2376.33, 653.27, 327.00, 653.38, 2403.33, 653.49, 316.00, 653.61, 2459.67, 653.72, 419.67, 653.83, 2450.33, 653.94, 279.67, 654.06, 2225.00, 654.17, 333.00, 654.28, 2199.67, 654.40, 387.67, 654.51, 2337.67, 654.62, 377.67, 654.73, 2276.00, 654.85, 259.67, 654.96, 2274.67, 655.07, 220.00, 655.18, 2219.67, 655.30, 292.67, 655.41, 2299.00, 655.52, 322.33, 655.63, 2224.00, 655.75, 293.33, 655.86, 2220.00, 655.97, 296.67, 656.08, 2334.00, 656.19, 246.67, 656.31, 2239.00, 656.42, 227.67, 656.53, 2288.67, 656.64, 348.00, 656.75, 2246.33, 656.87, 373.33, 656.98, 2175.33, 657.09, 250.00, 657.20, 2133.00, 657.31, 232.33, 657.42, 2191.00, 657.54, 258.33, 657.65, 2248.33, 657.76, 284.00, 657.87, 2223.00, 657.98, 225.00, 658.09, 2252.33, 658.20, 323.00, 658.32, 2212.33, 658.43, 284.67, 658.54, 2280.67, 658.65, 312.67, 658.76, 2240.33, 658.87, 284.00, 658.98, 2200.00, 659.09, 330.67, 659.20, 2134.33, 659.32, 295.33, 659.43, 2173.33, 659.54, 293.33, 659.65, 2122.33, 659.76, 292.33, 659.87, 2233.33, 659.98, 249.00, 660.09, 2160.00, 660.20, 268.67, 660.31, 2180.67, 660.42, 244.33, 660.53, 2167.67, 660.64, 289.00, 660.75, 2108.00, 660.86, 306.00, 660.97, 2117.00, 661.08, 310.00, 661.19, 2245.33, 661.30, 337.33, 661.42, 2197.33, 661.53, 353.67, 661.64, 2241.33, 661.75, 287.00, 661.86, 2164.00, 661.97, 321.33, 662.08, 2244.00, 662.18, 275.67, 662.29, 2371.33, 662.40, 399.00, 662.51, 2344.33, 662.62, 232.00, 662.73, 2359.00, 662.84, 377.67, 662.95, 2326.00, 663.06, 232.67, 663.17, 2262.67, 663.28, 356.67, 663.39, 2302.00, 663.50, 377.67, 663.61, 2229.00, 663.72, 276.33, 663.83, 2255.00, 663.94, 248.33, 664.05, 2295.67, 664.16, 347.67, 664.26, 2310.33, 664.37, 275.00, 664.48, 2384.00, 664.59, 350.67, 664.70, 2383.67, 664.81, 289.33, 664.92, 2378.67, 665.03, 377.00, 665.14, 2508.00, 665.25, 357.33, 665.35, 2537.33, 665.46, 438.00, 665.57, 2736.00, 665.68, 441.33, 665.79, 2939.67, 665.90, 469.33, 666.01, 3284.00, 666.11, 613.00, 666.22, 4927.00, 666.33, 1581.00, 666.44, 11753.67, 666.55, 3125.33, 666.66, 16871.67, 666.76, 3997.33, 666.87, 16807.00, 666.98, 3465.67, 667.09, 14626.00, 667.20, 2749.67, 667.30, 11620.67, 667.41, 2076.33, 667.52, 8486.00, 667.63, 1324.67, 667.74, 5059.33, 667.84, 744.67, 667.95, 2913.67, 668.06, 423.00, 668.17, 2609.00, 668.28, 469.33, 668.38, 2598.33, 668.49, 417.33, 668.60, 2431.00, 668.71, 418.67, 668.81, 2550.33, 668.92, 431.67, 669.03, 2441.33, 669.14, 304.67, 669.24, 2436.67, 669.35, 424.33, 669.46, 2431.67, 669.56, 441.33, 669.67, 2415.67, 669.78, 355.00, 669.89, 2333.33, 669.99, 382.00, 670.10, 2352.67, 670.21, 446.33, 670.31, 2316.33, 670.42, 394.33, 670.53, 2375.33, 670.63, 359.00, 670.74, 2362.67, 670.85, 385.33, 670.96, 2479.00, 671.06, 322.33, 671.17, 2474.33, 671.28, 417.67, 671.38, 2369.33, 671.49, 416.00, 671.60, 2348.33, 671.70, 318.00, 671.81, 2442.67, 671.91, 370.00, 672.02, 2452.00, 672.13, 368.33, 672.23, 2390.33, 672.34, 378.00, 672.45, 2439.33, 672.55, 423.00, 672.66, 2396.33, 672.76, 484.00, 672.87, 2334.00, 672.98, 449.33, 673.08, 2338.33, 673.19, 423.67, 673.29, 2358.67, 673.40, 490.00, 673.51, 2382.00, 673.61, 431.00, 673.72, 2316.00, 673.82, 409.00, 673.93, 2344.67, 674.04, 419.33, 674.14, 2393.00, 674.25, 460.33, 674.35, 2389.00, 674.46, 442.00, 674.56, 2451.33, 674.67, 478.67, 674.77, 2558.33, 674.88, 491.33, 674.99, 2579.67, 675.09, 429.33, 675.20, 2607.00, 675.30, 501.33, 675.41, 2736.00, 675.51, 562.33, 675.62, 2969.00, 675.72, 598.00, 675.83, 3204.00, 675.93, 698.00, 676.04, 3649.00, 676.14, 804.67, 676.25, 4322.67, 676.35, 1066.00, 676.46, 5566.33, 676.56, 1649.00, 676.67, 7730.67, 676.77, 2118.33, 676.88, 9089.33, 676.98, 2467.67, 677.09, 9611.00, 677.19, 2358.67, 677.29, 9096.00, 677.40, 2123.00, 677.50, 8134.67, 677.61, 1841.67, 677.71, 6711.00, 677.82, 1416.00, 677.92, 5464.33, 678.03, 1151.33, 678.13, 4296.33, 678.23, 881.33, 678.34, 3629.33, 678.44, 756.67, 678.55, 3268.67, 678.65, 694.33, 678.76, 3010.67, 678.86, 631.00, 678.96, 2923.33, 679.07, 616.33, 679.17, 2732.33, 679.28, 575.67, 679.38, 2527.00, 679.48, 598.33, 679.59, 2711.33, 679.69, 539.00, 679.80, 2617.00, 679.90, 522.00, 680.00, 2609.67, 680.11, 594.67, 680.21, 2625.67, 680.31, 511.33, 680.42, 2525.00, 680.52, 545.33, 680.62, 2577.67, 680.73, 540.33, 680.83, 2600.33, 680.93, 573.33, 681.04, 2610.67, 681.14, 577.00, 681.24, 2647.33, 681.35, 534.33, 681.45, 2617.33, 681.55, 584.33, 681.66, 2582.00, 681.76, 544.00, 681.86, 2645.67, 681.97, 547.33, 682.07, 2570.33, 682.17, 582.67, 682.28, 2771.00, 682.38, 664.67, 682.48, 3075.67, 682.59, 866.33, 682.69, 3553.00, 682.79, 906.67, 682.89, 4156.33, 683.00, 1050.33, 683.10, 4114.00, 683.20, 1098.00, 683.30, 3691.33, 683.41, 902.00, 683.51, 3439.00, 683.61, 852.00, 683.71, 3218.33, 683.82, 818.00, 683.92, 2937.67, 684.02, 693.33, 684.12, 2767.67, 684.23, 711.67, 684.33, 2689.33, 684.43, 623.00, 684.53, 2717.33, 684.64, 666.67, 684.74, 2540.00, 684.84, 712.00, 684.94, 2522.67, 685.04, 659.33, 685.15, 2601.67, 685.25, 605.67, 685.35, 2620.67, 685.45, 630.67, 685.55, 2644.00, 685.66, 663.67, 685.76, 2741.33, 685.86, 771.67, 685.96, 2757.33, 686.06, 748.00, 686.17, 2859.67, 686.27, 798.00, 686.37, 3054.00, 686.47, 807.33, 686.57, 3127.00, 686.67, 837.00, 686.78, 3271.33, 686.88, 921.33, 686.98, 3637.33, 687.08, 1013.67, 687.18, 3984.33, 687.28, 1197.67, 687.38, 4854.33, 687.49, 1668.00, 687.59, 6293.33, 687.69, 2161.67, 687.79, 8230.67, 687.89, 2894.67, 687.99, 10100.33, 688.09, 3375.33, 688.19, 10305.67, 688.30, 3248.33, 688.40, 9796.33, 688.50, 2892.00, 688.60, 8670.00, 688.70, 2588.33, 688.80, 7349.00, 688.90, 2137.67, 689.00, 6271.33, 689.10, 1717.33, 689.20, 5216.00, 689.30, 1477.67, 689.41, 4484.33, 689.51, 1247.33, 689.61, 4228.00, 689.71, 1118.33, 689.81, 3882.00, 689.91, 1057.00, 690.01, 3678.33, 690.11, 972.67, 690.21, 3544.67, 690.31, 1082.00, 690.41, 3455.33, 690.51, 1067.00, 690.61, 3370.00, 690.71, 1017.00, 690.81, 3351.00, 690.91, 967.67, 691.01, 3447.33, 691.11, 1011.33, 691.21, 3563.67, 691.31, 1198.33, 691.41, 3763.67, 691.51, 1189.33, 691.61, 4174.67, 691.71, 1227.33, 691.81, 4434.67, 691.91, 1287.00, 692.01, 4419.67, 692.11, 1408.00, 692.21, 4315.33, 692.31, 1233.67, 692.41, 4054.67, 692.51, 1186.33, 692.61, 3835.67, 692.71, 1108.00, 692.81, 3505.00, 692.91, 1142.00, 693.01, 3347.33, 693.11, 1013.67, 693.21, 3182.00, 693.31, 946.33, 693.41, 3065.67, 693.51, 990.67, 693.61, 3109.67, 693.71, 1015.67, 693.81, 3301.67, 693.91, 976.67, 694.01, 3227.33, 694.11, 1061.00, 694.21, 3428.67, 694.31, 1149.67, 694.40, 3445.67, 694.50, 1195.00, 694.60, 3669.33, 694.70, 1150.33, 694.80, 3859.67, 694.90, 1192.67, 695.00, 4012.00, 695.10, 1222.67, 695.20, 4197.00, 695.30, 1474.00, 695.40, 4681.00, 695.49, 1612.33, 695.59, 5113.00, 695.69, 1933.67, 695.79, 5877.67, 695.89, 2215.33, 695.99, 7021.33, 696.09, 2619.33, 696.19, 8630.33, 696.28, 3430.33, 696.38, 10581.33, 696.48, 4018.33, 696.58, 11778.33, 696.68, 4252.00, 696.78, 11737.00, 696.88, 4076.33, 696.97, 10570.33, 697.07, 3542.33, 697.17, 9343.67, 697.27, 3050.33, 697.37, 7930.00, 697.47, 2616.00, 697.57, 6855.00, 697.66, 2119.33, 697.76, 5764.33, 697.86, 1755.33, 697.96, 4995.33, 698.06, 1643.33, 698.15, 4436.67, 698.25, 1325.67, 698.35, 4104.67, 698.45, 1255.00, 698.55, 3773.67, 698.64, 1188.67, 698.74, 3612.00, 698.84, 1177.00, 698.94, 3340.67, 699.04, 1158.67, 699.13, 3332.00, 699.23, 1106.67, 699.33, 3183.00, 699.43, 977.33, 699.53, 3084.33, 699.62, 955.33, 699.72, 2858.67, 699.82, 991.00, 699.92, 2944.00, 700.01, 851.00, 700.11, 2900.67, 700.21, 906.67, 700.31, 2852.67, 700.40, 907.00, 700.50, 2813.00, 700.60, 939.67, 700.70, 2801.67, 700.79, 872.00, 700.89, 2761.67, 700.99, 951.33, 701.09, 2970.00, 701.18, 930.67, 701.28, 2824.67, 701.38, 890.00, 701.48, 2815.67, 701.57, 869.00, 701.67, 2766.00, 701.77, 893.67, 701.86, 2828.00, 701.96, 890.67, 702.06, 2753.33, 702.15, 880.00, 702.25, 2767.33, 702.35, 846.33, 702.45, 2708.00, 702.54, 803.00, 702.64, 2594.33, 702.74, 846.67, 702.83, 2620.67, 702.93, 837.00, 703.03, 2605.33, 703.12, 834.67, 703.22, 2555.33, 703.32, 868.33, 703.41, 2661.00, 703.51, 868.67, 703.61, 2726.33, 703.70, 856.67, 703.80, 2869.33, 703.90, 1030.00, 703.99, 2788.00, 704.09, 989.67, 704.19, 2942.33, 704.28, 937.33, 704.38, 2888.67, 704.48, 923.67, 704.57, 2783.00, 704.67, 921.33, 704.76, 2730.00, 704.86, 928.67, 704.96, 2665.00, 705.05, 801.33, 705.15, 2552.00, 705.25, 792.00, 705.34, 2595.67, 705.44, 810.00, 705.53, 2611.00, 705.63, 811.67, 705.73, 2441.33, 705.82, 806.67, 705.92, 2347.33, 706.01, 766.33, 706.11, 2381.67, 706.21, 781.67, 706.30, 2382.33, 706.40, 807.33, 706.49, 2432.67, 706.59, 758.33, 706.68, 2454.00, 706.78, 826.00, 706.88, 2412.67, 706.97, 811.67, 707.07, 2395.67, 707.16, 847.33, 707.26, 2295.67, 707.35, 751.67, 707.45, 2358.33, 707.54, 762.67, 707.64, 2429.00, 707.74, 766.00, 707.83, 2494.67, 707.93, 855.67, 708.02, 2492.00, 708.12, 796.67, 708.21, 2461.33, 708.31, 781.00, 708.40, 2389.00, 708.50, 738.33, 708.59, 2456.00, 708.69, 768.67, 708.78, 2456.67, 708.88, 769.33, 708.97, 2526.33, 709.07, 787.67, 709.16, 2407.67, 709.26, 793.33, 709.36, 2456.33, 709.45, 769.00, 709.55, 2333.00, 709.64, 804.00, 709.73, 2418.33, 709.83, 805.33, 709.92, 2448.00, 710.02, 766.67, 710.11, 2383.67, 710.21, 732.00, 710.30, 2383.00, 710.40, 759.33, 710.49, 2381.67, 710.59, 749.67, 710.68, 2376.00, 710.78, 833.00, 710.87, 2402.00, 710.97, 726.67, 711.06, 2267.00, 711.16, 740.67, 711.25, 2369.67, 711.35, 736.00, 711.44, 2266.33, 711.53, 775.67, 711.63, 2283.67, 711.72, 717.67, 711.82, 2309.00, 711.91, 663.33, 712.01, 2267.67, 712.10, 736.67, 712.19, 2376.67, 712.29, 696.67, 712.38, 2313.67, 712.48, 668.33, 712.57, 2228.67, 712.67, 705.33, 712.76, 2360.67, 712.85, 704.67, 712.95, 2137.33, 713.04, 709.33, 713.14, 2196.33, 713.23, 715.67, 713.32, 2274.67, 713.42, 707.33, 713.51, 2183.67, 713.61, 680.33, 713.70, 2184.00, 713.79, 719.33, 713.89, 2182.67, 713.98, 617.67, 714.08, 2179.33, 714.17, 713.00, 714.26, 2241.00, 714.36, 638.00, 714.45, 2234.67, 714.54, 708.00, 714.64, 2262.00, 714.73, 629.00, 714.82, 2262.33, 714.92, 613.67, 715.01, 2218.00, 715.11, 718.00, 715.20, 2196.67, 715.29, 654.67, 715.39, 2208.67, 715.48, 654.67, 715.57, 2156.33, 715.67, 644.00, 715.76, 2137.33, 715.85, 695.33, 715.95, 2178.00, 716.04, 671.00, 716.13, 2278.67, 716.23, 663.00, 716.32, 2108.33, 716.41, 735.33, 716.51, 2161.33, 716.60, 713.00, 716.69, 2139.00, 716.78, 637.00, 716.88, 2218.67, 716.97, 707.33, 717.06, 2194.33, 717.16, 667.00, 717.25, 2270.67, 717.34, 630.33, 717.44, 2143.00, 717.53, 641.00, 717.62, 2263.00, 717.71, 571.67, 717.81, 2125.00, 717.90, 703.67, 717.99, 2178.00, 718.09, 660.00, 718.18, 2136.00, 718.27, 614.00, 718.36, 2143.00, 718.46, 653.67, 718.55, 2206.67, 718.64, 680.33, 718.73, 2065.33, 718.83, 673.33, 718.92, 2090.33, 719.01, 680.33, 719.10, 1964.33, 719.20, 674.33, 719.29, 2057.33, 719.38, 583.00, 719.47, 2198.67, 719.57, 661.67, 719.66, 2070.33, 719.75, 685.33, 719.84, 2100.67, 719.94, 629.67, 720.03, 2121.67, 720.12, 538.33, 720.21, 2063.33, 720.30, 602.67, 720.40, 2078.00, 720.49, 549.33, 720.58, 2012.00, 720.67, 623.00, 720.77, 2030.00, 720.86, 632.33, 720.95, 2066.00, 721.04, 629.00, 721.13, 2040.33, 721.23, 635.33, 721.32, 2063.00, 721.41, 627.33, 721.50, 2025.67, 721.59, 598.00, 721.69, 2017.67, 721.78, 557.00, 721.87, 2117.67, 721.96, 611.33, 722.05, 2033.67, 722.14, 657.00, 722.24, 2051.00, 722.33, 680.33, 722.42, 2020.33, 722.51, 598.33, 722.60, 2109.00, 722.69, 489.00, 722.79, 2057.00, 722.88, 581.67, 722.97, 2140.67, 723.06, 606.00, 723.15, 2002.67, 723.24, 584.33, 723.33, 2140.67, 723.43, 639.33, 723.52, 2024.00, 723.61, 626.33, 723.70, 1937.67, 723.79, 551.33, 723.88, 2097.67, 723.97, 544.00, 724.07, 2072.00, 724.16, 586.33, 724.25, 1921.67, 724.34, 504.33, 724.43, 1981.67, 724.52, 548.67, 724.61, 1921.00, 724.70, 536.67, 724.80, 1987.33, 724.89, 511.00, 724.98, 1994.33, 725.07, 580.67, 725.16, 1880.00, 725.25, 551.67, 725.34, 1964.33, 725.43, 549.00, 725.52, 1839.00, 725.61, 490.67, 725.71, 1976.67, 725.80, 595.67, 725.89, 1906.33, 725.98, 532.67, 726.07, 1871.67, 726.16, 482.67, 726.25, 1905.33, 726.34, 478.67, 726.43, 1932.67, 726.52, 564.00, 726.61, 1929.67, 726.70, 500.33, 726.79, 1761.67, 726.88, 499.00, 726.98, 1892.67, 727.07, 488.33, 727.16, 1981.33, 727.25, 609.33, 727.34, 1941.33, 727.43, 567.00, 727.52, 2006.00, 727.61, 529.67, 727.70, 1964.33, 727.79, 502.67, 727.88, 1956.67, 727.97, 502.33, 728.06, 1900.00, 728.15, 535.00, 728.24, 1959.67, 728.33, 531.00, 728.42, 1928.33, 728.51, 488.00, 728.60, 1878.67, 728.69, 558.33, 728.78, 1957.33, 728.87, 510.00, 728.96, 1924.67, 729.05, 527.33, 729.14, 1845.33, 729.23, 530.33, 729.32, 1857.33, 729.41, 435.33, 729.50, 1867.00, 729.59, 529.00, 729.68, 1878.33, 729.77, 506.00, 729.86, 1822.67, 729.95, 533.00, 730.04, 1844.00, 730.13, 544.00, 730.22, 1846.33, 730.31, 494.00, 730.40, 1949.33, 730.49, 500.00, 730.58, 1899.67, 730.67, 579.00, 730.76, 1855.00, 730.85, 548.67, 730.94, 1993.67, 731.03, 488.00, 731.12, 1889.67, 731.21, 509.33, 731.30, 1828.00, 731.39, 460.67, 731.48, 1839.33, 731.57, 416.67, 731.66, 1941.00, 731.74, 485.33, 731.83, 1870.33, 731.92, 541.00, 732.01, 1907.33, 732.10, 472.33, 732.19, 1834.67, 732.28, 494.00, 732.37, 1863.00, 732.46, 460.67, 732.55, 1849.33, 732.64, 514.67, 732.73, 1941.00, 732.82, 540.33, 732.91, 1871.67, 732.99, 516.33, 733.08, 1865.67, 733.17, 487.00, 733.26, 1944.00, 733.35, 584.00, 733.44, 1896.33, 733.53, 463.33, 733.62, 1831.33, 733.71, 491.00, 733.80, 1873.00, 733.88, 545.00, 733.97, 1903.00, 734.06, 462.67, 734.15, 1888.00, 734.24, 511.67, 734.33, 1959.00, 734.42, 471.00, 734.51, 1809.67, 734.60, 461.00, 734.68, 1765.33, 734.77, 515.00, 734.86, 1885.00, 734.95, 485.67, 735.04, 1743.00, 735.13, 480.67, 735.22, 1887.00, 735.30, 485.67, 735.39, 1923.33, 735.48, 503.33, 735.57, 1880.33, 735.66, 443.67, 735.75, 1949.67, 735.84, 467.33, 735.92, 1970.33, 736.01, 489.67, 736.10, 1961.67, 736.19, 597.67, 736.28, 2049.67, 736.37, 507.67, 736.46, 2056.33, 736.54, 541.67, 736.63, 2082.33, 736.72, 521.33, 736.81, 1968.00, 736.90, 540.33, 736.98, 1950.33, 737.07, 504.00, 737.16, 1968.00, 737.25, 454.67, 737.34, 1863.33, 737.43, 490.33, 737.51, 1934.00, 737.60, 482.67, 737.69, 1937.33, 737.78, 455.67, 737.87, 1909.00, 737.95, 517.33, 738.04, 1831.00, 738.13, 563.33, 738.22, 1877.33, 738.31, 473.67, 738.39, 1839.00, 738.48, 583.33, 738.57, 1875.33, 738.66, 517.00, 738.74, 1842.33, 738.83, 377.33, 738.92, 1868.00, 739.01, 521.67, 739.10, 1741.00, 739.18, 501.33, 739.27, 1792.67, 739.36, 499.67, 739.45, 1832.67, 739.53, 470.00, 739.62, 1824.67, 739.71, 526.67, 739.80, 1799.67, 739.88, 501.67, 739.97, 1819.33, 740.06, 511.33, 740.15, 1753.67, 740.23, 470.67, 740.32, 1761.33, 740.41, 467.33, 740.50, 1708.33, 740.58, 464.33, 740.67, 1781.33, 740.76, 463.33, 740.85, 1773.00, 740.93, 434.67, 741.02, 1743.00, 741.11, 434.00, 741.20, 1853.67, 741.28, 455.33, 741.37, 1824.00, 741.46, 523.33, 741.54, 1776.33, 741.63, 495.00, 741.72, 1822.67, 741.81, 516.67, 741.89, 1878.67, 741.98, 516.67, 742.07, 1803.00, 742.15, 494.67, 742.24, 1886.67, 742.33, 493.33, 742.42, 1820.67, 742.50, 521.00, 742.59, 1846.33, 742.68, 493.33, 742.76, 1929.67, 742.85, 557.33, 742.94, 1915.00, 743.02, 487.33, 743.11, 1942.00, 743.20, 550.67, 743.28, 1992.67, 743.37, 636.00, 743.46, 1955.33, 743.54, 546.00, 743.63, 1941.33, 743.72, 521.67, 743.80, 1948.00, 743.89, 574.00, 743.98, 1956.67, 744.06, 543.33, 744.15, 1969.00, 744.24, 571.67, 744.32, 1856.67, 744.41, 577.00, 744.50, 1834.67, 744.58, 533.67, 744.67, 1826.67, 744.76, 509.67, 744.84, 1769.33, 744.93, 575.00, 745.02, 1679.67, 745.10, 495.33, 745.19, 1832.67, 745.27, 470.33, 745.36, 1704.67, 745.45, 522.67, 745.53, 1717.00, 745.62, 526.67, 745.71, 1658.33, 745.79, 520.00, 745.88, 1608.00, 745.96, 498.33, 746.05, 1587.67, 746.14, 521.33, 746.22, 1685.67, 746.31, 463.33, 746.40, 1654.33, 746.48, 472.67, 746.57, 1649.33, 746.65, 481.00, 746.74, 1641.33, 746.83, 513.00, 746.91, 1624.00, 747.00, 512.67, 747.08, 1725.00, 747.17, 517.33, 747.26, 1650.33, 747.34, 520.67, 747.43, 1623.33, 747.51, 489.67, 747.60, 1739.33, 747.68, 519.33, 747.77, 1745.33, 747.86, 465.67, 747.94, 1629.00, 748.03, 493.67, 748.11, 1751.33, 748.20, 521.00, 748.28, 1811.33, 748.37, 502.67, 748.46, 1704.67, 748.54, 491.00, 748.63, 1791.33, 748.71, 597.33, 748.80, 1787.33, 748.88, 612.33, 748.97, 1945.00, 749.05, 555.33, 749.14, 1967.00, 749.23, 611.67, 749.31, 1951.33, 749.40, 668.67, 749.48, 1968.33, 749.57, 590.00, 749.65, 1834.67, 749.74, 555.33, 749.82, 1852.33, 749.91, 535.33, 749.99, 1816.00, 750.08, 551.67, 750.16, 1818.33, 750.25, 596.00, 750.33, 1695.33, 750.42, 484.67, 750.51, 1850.67, 750.59, 569.00, 750.68, 1706.33, 750.76, 571.33, 750.85, 1764.00, 750.93, 521.67, 751.02, 1732.67, 751.10, 463.33, 751.19, 1717.00, 751.27, 524.33, 751.36, 1738.00, 751.44, 513.67, 751.53, 1636.67, 751.61, 481.33, 751.70, 1691.67, 751.78, 503.67, 751.87, 1715.00, 751.95, 488.33, 752.04, 1676.00, 752.12, 462.33, 752.20, 1743.00, 752.29, 550.33, 752.37, 1757.67, 752.46, 576.67, 752.54, 1706.67, 752.63, 536.00, 752.71, 1708.33, 752.80, 531.33, 752.88, 1811.67, 752.97, 564.00, 753.05, 1746.67, 753.14, 549.33, 753.22, 1729.33, 753.31, 596.67, 753.39, 1843.00, 753.47, 499.33 ]
    x, y = [], []
    for i in range(0, len(data) - 1, 2):
        x.append(data[i])
        y.append(data[i+1])

    imx385    = IMX385()
    binned    = imx385.bin2x2(y)
    corrected = imx385.correct(y, x)
    both      = imx385.bin2x2(corrected)

    print("wavelength, raw, binned, corrected, both")
    for i in range(len(x)):
        print(f"{x[i]:.2f}, {y[i]:.2f}, {binned[i]:.2f}, {corrected[i]:.2f}, {both[i]:.2f}")