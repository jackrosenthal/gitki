import flask
import gitki.gitki as gitki
import yaml

with open('devserver.yaml') as f:
    app = gitki.build_app(yaml.safe_load(f))
