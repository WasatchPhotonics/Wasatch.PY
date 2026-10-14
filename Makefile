help:
	@echo "Supported targets:"
	@echo "  doc               (render Doxygen)"
	@echo "  clean             (delete artifacts)"
	@echo "  cloc              (count SLOC)"
	@echo "  publish           (flit --> pypi)"
	@echo "  publish-test      (flit --> testpypi)"
	@echo "  pip-install-local"                

.PHONY: doc docs clean cloc publish publish-test 

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

# run after "python -m build"
publish-test:
	python -m twine upload --repository testpypi dist/wasatch*

# run after "python -m build"
publish:
	python -m twine upload dist/wasatch*

pylint:
	@pylint wasatch

flake8:
	@find wasatch -name \*.py | sort | xargs flake8 | tee flake8.out 
	@echo && echo Counts:
	@awk -F": " '{print $$2}' < flake8.out | sort | uniq -c | sort -n
	@rm -f flake8.out
