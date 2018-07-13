help:
	@echo "Supported targets:"
	@echo "  doc    (render Doxygen)"
	@echo "  clean  (delete artifacts)"

.PHONY: doc clean

doc:
	@echo "Rendering Doxygen..."
	@mkdir -p doxygen
	@doxygen 1>doxygen.out 2>doxygen.err
	#@cat doxygen.out
	@cat doxygen.err

clean:
	@rm -rf doxygen     \
            doxygen.out \
            doxygen.err
	@find . -name \*.pyc -exec rm {} \;
