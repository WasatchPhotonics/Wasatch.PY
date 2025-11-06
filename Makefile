help:
	@echo "Supported targets:"
	@echo "  doc               (render Doxygen)"
	@echo "  clean             (delete artifacts)"
	@echo "  cloc              (count SLOC)"
	@echo "  publish           (flit --> pypi)"
	@echo "  publish-test      (flit --> testpypi)"
	@echo "  pip-install-local"                

.PHONY: doc docs clean cloc publish publish-test pip-install-local

cloc:
	@cloc --include-lang=Python .

doc docs:
	@echo "Rendering Doxygen..."
	@mkdir -p doxygen
	@doxygen 1>doxygen.out 2>doxygen.err
	@cat doxygen.err

clean:
	@rm -rf doxygen     \
            doxygen.out \
            doxygen.err
	@find . -name \*.pyc -exec rm {} \;

pip-install-local:
	pip install $$PWD

publish-test:
	@echo REMINDER to keep .toml in sync with __init__.py!
	@echo 
	flit publish --repository testpypi

publish:
	@echo REMINDER to keep .toml in sync with __init__.py!
	@echo 
	flit publish
