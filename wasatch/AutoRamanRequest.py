class AutoRamanRequest:
    
    def __init__(self, max_ms         = 10000, 
                       start_integ_ms = 100, 
                       start_gain_db  = 0,
                       max_integ_ms   = 2000,
                       min_integ_ms   = 10,
                       max_gain_db    = 32,
                       min_gain_db    = 0,
                       target_counts  = 45000,
                       max_counts     = 50000,
                       min_counts     = 40000,
                       max_factor     = 5,
                       drop_factor    = 0.5,
                       saturation     = 65000):

        self.max_ms         = max_ms         
        self.start_integ_ms = start_integ_ms 
        self.start_gain_db  = start_gain_db  
        self.max_integ_ms   = max_integ_ms   
        self.min_integ_ms   = min_integ_ms   
        self.max_gain_db    = max_gain_db    
        self.min_gain_db    = min_gain_db    
        self.target_counts  = target_counts  
        self.max_counts     = max_counts     
        self.min_counts     = min_counts     
        self.max_factor     = max_factor     
        self.drop_factor    = drop_factor    
        self.saturation     = saturation     

    def __repr__(self):
        return f"AutoRamanRequest <max_ms {self.max_ms}>"
