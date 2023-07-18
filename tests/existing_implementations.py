
# executable known past correct implementations

import numpy

class utils:
    # commit eaf1f60945be2b51baa1b0fe757e710cfa392f5b 
    def moving_average(a, n):
        ret = numpy.cumsum(a, dtype=float)
        ret[n:] = ret[n:] - ret[:-n]
        return ret[n - 1:] / n 

    # commit eaf1f60945be2b51baa1b0fe757e710cfa392f5b 
    def apply_boxcar(a, half_width):
        if a is None:
            return None
        if half_width < 1:
            return a
        return numpy.hstack((a[0:half_width], 
                             utils.moving_average(a, half_width * 2 + 1), 
                             a[-half_width:])).ravel()
