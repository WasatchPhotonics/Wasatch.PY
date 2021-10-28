# Notes About PyPi

- PyPi packages only have one index. This means there is only ever one wasatch or scipy or numpy etc. 
The added constratint of this is that whenever a new publish is made the version number MUST be bumped, so it is important to get the upload correct the first time.
- There is a testpypi that is good to use in order to avoid issues with publishing so the version doesn't have to be bumped in the event of an issue
- After uploading to the testpypi you can pip install from it as in the following example ```pip install -i https://test.pypi.org/simple/ wasatch```
- For more info about making a package, This is a good resource https://realpython.com/pypi-publish-python-package/ the info on flit is all the way at the end of the article
- You need 2 separte accounts, 1 for pypi and 1 for testpypi
- Owners/Managers for repos can be added in their settings when you log in

# Uploading with FLIT

We use flit to upload out package. You shouldn't need to run flit init since that has already setup the .toml and other initialization files are included.
To make uploading easier, an example .pypirc has been included. This needs to be moved to your $HOME or ~ directory in order to allow you to use it.
This lets you run ```flit publish --repository testpypi``` so you can upload to the test index before uploading to pypi.
When you are ready to publish run ```flit publish``` or ```flit publish --repository pypi```