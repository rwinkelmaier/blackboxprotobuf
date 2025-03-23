all: check test_py2 test_py3
setup_py2:
	rm -rf /tmp/bbpb-python2-tmp
	python2 -m virtualenv /tmp/bbpb-python2-tmp
	. /tmp/bbpb-python2-tmp/bin/activate
	python2 -m pip install -r tests/requirements-python2-dev.txt
test_py2: setup_py2
	python2 -m pytest tests/py_test/
test_py3:
	poetry env use python3
	poetry run python -m pytest tests/py_test/
format:
	poetry run black .
check:
	poetry run mypy --strict --disable-bytearray-promotion blackboxprotobuf/
prepublish:
	poetry build
	poetry config repositories.test-pypi https://test.pypi.org/legacy/
	# poetry config pypi-token.test-pypi TOKEN
	# poetry publish -r test-pypi
	# poetry config pypi-token.pypi TOKEN
	# poetry publish
build_docker_py3:
	docker build -t bbpb_dev_py3 -f tests/Dockerfile.py3-dev .
run_docker_py3:
	docker run -it --rm -v ${PWD}:/app/lib/ bbpb_dev_py3 /bin/bash
build_docker_py2:
	docker build -t bbpb_dev_py2 -f tests/Dockerfile.py2-dev .
run_docker_py2:
	docker run -it --rm -v ${PWD}:/app/lib/ bbpb_dev_py2 /bin/bash
