help:
	@echo "Supported targets:"
	@echo "  doc    (render Doxygen)"
	@echo "  clean  (delete artifacts)"
	@echo "  cloc   (count SLOC)"

.PHONY: doc docs clean cloc

cloc:
	@cloc --include-lang=Python .

doc docs:
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

################################################################################
# The following are provided as convenience / documentation for people not 
# familiar with building PyPi packages
################################################################################

pip-install-local:
	pip install $$PWD

