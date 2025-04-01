class AreaScanImage:
    def __init__(self, data, width, height, fmt="Mono16"):
        self.data = data
        self.width = width
        self.height = height
        self.fmt = fmt

    def __repr__(self):
        return f"AreaScanImage<width {self.width}, height {self.height}, fmt {self.fmt}>"
