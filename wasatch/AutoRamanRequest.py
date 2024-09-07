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
    LASER_DELAY_MS = 4000
    SATURATION     = 65000
    
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
                       laser_delay_ms = None,
                       saturation     = None):

        # this seems like it could be shorter, but Python doesn't allow 
        # 'max_ms = AutoRamanRequest.MAX_MS` constructor parameters
        self.max_ms         = max_ms         if max_ms         is not None else MAX_MS         
        self.start_integ_ms = start_integ_ms if start_integ_ms is not None else START_INTEG_MS 
        self.start_gain_db  = start_gain_db  if start_gain_db  is not None else START_GAIN_DB  
        self.max_integ_ms   = max_integ_ms   if max_integ_ms   is not None else MAX_INTEG_MS   
        self.min_integ_ms   = min_integ_ms   if min_integ_ms   is not None else MIN_INTEG_MS   
        self.max_gain_db    = max_gain_db    if max_gain_db    is not None else MAX_GAIN_DB    
        self.min_gain_db    = min_gain_db    if min_gain_db    is not None else MIN_GAIN_DB    
        self.target_counts  = target_counts  if target_counts  is not None else TARGET_COUNTS  
        self.max_counts     = max_counts     if max_counts     is not None else MAX_COUNTS     
        self.min_counts     = min_counts     if min_counts     is not None else MIN_COUNTS     
        self.max_factor     = max_factor     if max_factor     is not None else MAX_FACTOR     
        self.drop_factor    = drop_factor    if drop_factor    is not None else DROP_FACTOR    
        self.laser_delay_ms = laser_delay_ms if laser_delay_ms is not None else LASER_DELAY_MS 
        self.saturation     = saturation     if saturation     is not None else SATURATION     

    def __repr__(self):
        return f"AutoRamanRequest <max_ms {self.max_ms}>"
