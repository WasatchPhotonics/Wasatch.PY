import json
import array
import numpy as np
import logging

log = logging.getLogger(__name__)

class WasatchJSONEncoder(json.JSONEncoder):
    """
    Optimizes JSON output format by keeping simple lists/arrays on a single line.

    Gemini deserves a hat-tip on this. I Googled something like "python 
    json.dumps list on one line", and it generated a basic CompactArrayEncoder. 
    I've re-written about 50% of it, but props where due.
    """

    def __init__(self, *args, **kwargs):
        """
        @param set_progress_bar (Input) optional callback function to, for 
               instance, enlighten.ui.ProgressBarFeature.set
        """
        self.set_progress_bar = kwargs.pop('set_progress_bar', None)
        self.sort_keys = kwargs.get('sort_keys', False)
        self.indent_per_level = kwargs.get('indent', 2)

        self.indentation_level = 0
        self.seen_types = set()

        super().__init__(*args, **kwargs)

    def iterencode(self, o, _one_shot=False):

        # log.debug(f"iterencode: o = {o}")

        # handle lists and tuples
        if isinstance(o, (list, tuple, set)):

            if len(o) == 0:
                yield "[]" 

            elif all(isinstance(v, (int, float, str, bool, np.float32, np.float64)) for v in o):
                # if they're all simple scalars, flatten them onto one line
                yield '[' + ', '.join([json.dumps(v) for v in o]) + ']'

            else:
                # apparently this is a non-empty list with one or more complicated values, so 
                # render the list with each element on one line

                # start the list and increase indentation
                yield '[\n'
                self.indentation_level += 1
                space = ' ' * (self.indent_per_level * self.indentation_level)

                # iterate the list, one element per line
                count = len(o)
                for i, v in enumerate(o):
                    if self.set_progress_bar and self.indentation_level == 1:
                        self.set_progress_bar(100 * i / count)

                    # Recursively process the value
                    yield f"{space}"
                    yield from self.iterencode(v)
                    if i < len(o) - 1:
                        yield ',\n'

                # restore indentation and close the list
                if self.set_progress_bar and self.indentation_level == 1:
                    self.set_progress_bar(100)
                self.indentation_level -= 1
                closing_space = ' ' * (self.indent_per_level * self.indentation_level)
                yield f'\n{closing_space}]'

        elif isinstance(o, (bytearray, array.array)):
            yield json.dumps(str(o))
            # o = list(o)
            # yield '[' + ', '.join([json.dumps(x) for x in o]) + ']'

        # always recurse dictionaries
        elif isinstance(o, dict):
            if not o:
                yield '{}'
            else:
                yield '{\n'
                self.indentation_level += 1
                space = ' ' * (self.indent_per_level * self.indentation_level)
                
                for i, (k, v) in enumerate(sorted(o.items())):
                    # display the item key
                    yield f"{space}{json.dumps(k)}: "

                    # recurse into the item value
                    yield from self.iterencode(v)
                    if i < len(o) - 1:
                        yield ',\n'
                
                self.indentation_level -= 1
                closing_space = ' ' * (self.indent_per_level * self.indentation_level)
                yield f'\n{closing_space}}}'

        else:
            # it's not a list, tuple, set, or dict

            # classname = type(o).__name__
            # if classname not in self.seen_types:
            #     log.debug(f"iterencode: classname {classname}: {o}")
            #     self.seen_types.add(classname)

            # if it has a to_dict() method, call that
            if hasattr(o, "to_dict"):
                yield from self.iterencode(o.to_dict())
            else:
                # pass it upstream and hope the normal json.dumps knows how to handle it
                yield from super().iterencode(o, _one_shot)
