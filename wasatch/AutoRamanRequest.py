class AutoRamanRequest:

    MAX_MS         = 10000
    START_INTEG_MS = 100
    START_GAIN_DB  = 0
    MAX_INTEG_MS   = 2000
    MIN_INTEG_MS   = 10
    MAX_GAIN_DB    = 32
    MIN_GAIN_DB    = 0
    TARGET_COUNTS  = 45000
    MAX_COUNTS     = 50000
    MIN_COUNTS     = 40000
    MAX_FACTOR     = 5
    DROP_FACTOR    = 0.5
    SATURATION     = 65000
    MAX_AVG        = 10
    
    def __init__(self, max_ms         = None,
                       start_integ_ms = None,
                       start_gain_db  = None,
                       max_integ_ms   = None,
                       min_integ_ms   = None,
                       max_gain_db    = None,
                       min_gain_db    = None,
                       target_counts  = None,
                       max_counts     = None,
                       min_counts     = None,
                       max_factor     = None,
                       drop_factor    = None,
                       saturation     = None,
                       max_avg        = None):

        # this seems like it could be shorter, but Python doesn't allow 
        # 'max_ms = AutoRamanRequest.MAX_MS` constructor parameters
        self.max_ms         = max_ms         if max_ms         is not None else self.MAX_MS         
        self.start_integ_ms = start_integ_ms if start_integ_ms is not None else self.START_INTEG_MS 
        self.start_gain_db  = start_gain_db  if start_gain_db  is not None else self.START_GAIN_DB  
        self.max_integ_ms   = max_integ_ms   if max_integ_ms   is not None else self.MAX_INTEG_MS   
        self.min_integ_ms   = min_integ_ms   if min_integ_ms   is not None else self.MIN_INTEG_MS   
        self.max_gain_db    = max_gain_db    if max_gain_db    is not None else self.MAX_GAIN_DB    
        self.min_gain_db    = min_gain_db    if min_gain_db    is not None else self.MIN_GAIN_DB    
        self.target_counts  = target_counts  if target_counts  is not None else self.TARGET_COUNTS  
        self.max_counts     = max_counts     if max_counts     is not None else self.MAX_COUNTS     
        self.min_counts     = min_counts     if min_counts     is not None else self.MIN_COUNTS     
        self.max_factor     = max_factor     if max_factor     is not None else self.MAX_FACTOR     
        self.drop_factor    = drop_factor    if drop_factor    is not None else self.DROP_FACTOR    
        self.saturation     = saturation     if saturation     is not None else self.SATURATION     
        self.max_avg        = max_avg        if max_avg        is not None else self.MAX_AVG

    def __repr__(self):
        return f"AutoRamanRequest <max_ms {self.max_ms}, start_integ_ms {self.start_integ_ms}, start_gain_db {self.start_gain_db}, max_integ_ms {self.max_integ_ms}, min_integ_ms {self.min_integ_ms}, max_gain_db {self.max_gain_db}, min_gain_db {self.min_gain_db}, target_counts {self.target_counts}, max_counts {self.max_counts}, min_counts {self.min_counts}, max_factor {self.max_factor}, drop_factor {self.drop_factor}, saturation {self.saturation}, max_avg {self.max_avg}>"
