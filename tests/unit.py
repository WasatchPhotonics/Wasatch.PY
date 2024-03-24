
# Wasatch.PY unit tests
# Run this program without arguments in your development env to generate a test report.

# add wasatch folder to import path independent of cwd
import sys
import os
filefolder = os.path.dirname(__file__)
sys.path.append(filefolder + os.sep + ".." + os.sep + "wasatch")

# unnecessary but fun
from shutil import get_terminal_size
EQUALBREAK = '='*get_terminal_size((72,0))[0]
LINEBREAK = '-'*get_terminal_size((72,0))[0]

from random import random, seed
# be deterministic
seed(0)
import existing_implementations

# modules to test
import utils

def gen_spectra():
    """
    Copied from pyusb-virtSpec/usb/wasatchConfig.py 
    """
    peaks = [(823.163, 2.6e2), (828.012, 1.2e2), (840.919, .05e2), (881.941, 5.8e2), (895.225, .8e2), (904.545, .78e2), (916.265, .53e2)]
    peaks = [((x-800) * (1024/200), y) for (x,y) in peaks]
    noise = 10
    return [int(sum([p[1]/(1+(x-p[0])**4) for p in peaks])+noise*random()) for x in range(1024)]

def compare_numeric_arrays(actual, known):

    if len(known) < len(actual):
        print("Arrays do not match length.")
        print("There are %s-%s=%s extra entries" % (len(actual), len(known), len(actual)-len(known)))
    if len(known) > len(actual):
        print("Arrays do not match length.")
        print("There are missing %s-%s=%s extra entries" % (len(known), len(actual), len(known)-len(actual)))

    tlen = min(len(known), len(actual))

    exact = 0
    within5percent = 0
    error = 0

    for i in range(tlen):
        error += (known[i]-actual[i])**2

        if known[i] == actual[i]:
            exact += 1
        elif actual[i] and abs(known[i]-actual[i])/actual[i] < .05:
            within5percent += 1

    print("%.2f%% (%d entries) is an exact match." % (100*exact/tlen, exact))
    print("%.2f%% (%d entries) does not match but is within 5%%." % (100*within5percent/tlen, within5percent))
    print("%.2f%% (%d entries) is more than 5%% different." % (100*(tlen-within5percent-exact)/tlen, tlen-within5percent-exact))
    print("Error factor: %.2f" % error**.5)

if __name__ == "__main__":
    print(EQUALBREAK)
    print("Comparing utils.apply_boxcar with known implementation.")
    spectrum = gen_spectra()
    print(LINEBREAK)
    print('Sim spectra with half_width=5')
    known_out = existing_implementations.utils.apply_boxcar(spectrum, 5)
    actual_out = utils.apply_boxcar(spectrum, 5)
    compare_numeric_arrays(actual_out, known_out)
    print(LINEBREAK)
    print('Sim spectra with half_width=20')
    known_out = existing_implementations.utils.apply_boxcar(spectrum, 20)
    actual_out = utils.apply_boxcar(spectrum, 20)
    # compare_arrays prints a sub-report based on known vs actual.
    compare_numeric_arrays(actual_out, known_out)
    print(LINEBREAK)
    print('Sim spectra with half_width=100')
    known_out = existing_implementations.utils.apply_boxcar(spectrum, 100)
    actual_out = utils.apply_boxcar(spectrum, 100)
    # compare_arrays prints a sub-report based on known vs actual.
    compare_numeric_arrays(actual_out, known_out)
    print(EQUALBREAK)



