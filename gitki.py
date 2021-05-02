import flask
import pathlib
import subprocess
import tempfile

import gitkitext
from werkzeug.utils import xhtml


class NotFoundError(Exception):
    pass


def git_index_head(index):
        result = subprocess.run(
            ['git', '-C', index, 'log', '-n1', '--format=%H'],
            stdout=subprocess.PIPE, check=True, encoding='utf-8')
        return result.stdout.rstrip()


def git_stage_changes(index, path, contents):
    index = pathlib.Path(index)
    fpath = index / path

    mode = 'w'
    if isinstance(contents, bytes):
        mode = 'wb'

    with open(fpath, mode) as f:
        f.write(contents)

    subprocess.run(['git', '-C', index, 'add', fpath], check=True)


def git_remove(index, path):
    index = pathlib.Path(index)
    fpath = index / path

    subprocess.run(['git', '-C', index, 'rm', fpath], check=True)


def git_commit(index, message, author):
    author_name, author_email = author
    env = {
        'GIT_AUTHOR_NAME': author_name,
        'GIT_AUTHOR_EMAIL': author_email,
    }
    subprocess.run(['git', '-C', index, 'commit', '-m', message],
                   env=env, check=True)
    return git_index_head(index)


def git_cherry_pick(index, revision):
    subprocess.run(['git', '-C', index, 'cherry-pick', revision], check=True)
    return git_index_head(index)


def git_reset(index, revision):
    subprocess.run(['git', '-C', index, 'reset', '--hard', revision],
                   check=True)


class GitWorktree(tempfile.TemporaryDirectory):
    def __init__(self, repo, *args, revision='HEAD', **kwargs):
        self.repo = repo
        self.revision = revision
        super().__init__(*args, **kwargs)

    def __enter__(self):
        self.wt_dir = pathlib.Path(super().__enter__())
        subprocess.run(
            ['git', '-C', self.repo, 'worktree', 'add', '-d', self.wt_dir,
             self.revision],
            check=True)
        return self.wt_dir

    def __exit__(self, exc_type, exc_val, exc_tb):
        subprocess.run(
            ['git', '-C', self.repo, 'worktree', 'remove', self.wt_dir],
            check=True)
        super().__exit__(exc_type, exc_val, exc_tb)


class Gitki:
    def __init__(self, repo):
        self.repo = pathlib.Path(repo)

    @property
    def index_head(self):
        return git_index_head(self.repo)

    def get_contents_at_revision(self, name, revision='HEAD', encoding='utf-8'):
        try:
            result = subprocess.run(
                ['git', '-C', self.repo, 'show', '{}:{}'.format(revision, name)],
                stdout=subprocess.PIPE, check=True, encoding=encoding,
                errors='replace')
        except subprocess.CalledProcessError:
            raise NotFoundError(
                'The file {} does not exist at revision {}.'.format(
                    name, revision))
        return result.stdout

    def render_page(self, name, revision='HEAD'):
        page_raw = self.get_contents_at_revision('{}.txt'.format(name),
                                                 revision='HEAD')

        return name, gitkitext.to_html(gitkitext.parse(page_raw))

    def worktree(self, revision='HEAD'):
        return GitWorktree(self.repo, revision=revision)

    def update_file(self, name, author, contents, revision='HEAD',
                    message=None):
        with self.worktree(revision=revision) as worktree:
            if contents is None:
                git_remove(worktree, name)
            else:
                git_stage_changes(worktree, name, contents)

            if not message:
                message = 'Update {}'.format(name)

            new_rev = git_commit(worktree, message, author)

            # First rebase on main index to shake out merge conflicts
            git_reset(worktree, self.index_head)
            new_rev = git_cherry_pick(worktree, new_rev)

            # Next, submit into the main index
            git_cherry_pick(self.repo, new_rev)


def build_app(config):
    app = flask.Flask(__name__)
    app.config.from_mapping(config)

    gitki = Gitki(app.config['GITKI_HOME'])

    @app.route('/page/<name>/edit', methods=['POST'])
    def edit_submit(name):
        author = ('Alyssa P. Hacker', 'aphacker@example.org')
        gitki.update_file('{}.txt'.format(name),
                          author, flask.request.form.get('contents'),
                          revision=flask.request.form.get('revision'),
                          message=flask.request.form.get('message'))
        return flask.redirect(flask.url_for('page', name=name))

    @app.route('/page/<name>/edit', methods=['GET'])
    def edit(name):
        revision = flask.request.args.get('revision', gitki.index_head)

        try:
            page_contents = gitki.get_contents_at_revision(
                '{}.txt'.format(name), revision=revision)
            default_message = 'Updated {}'.format(name)
        except NotFoundError:
            page_contents = ''
            default_message = 'Created new page {}'.format(name)

        return xhtml.body(
            xhtml.h1('Editing ', name),
            xhtml.form(
                xhtml.input(type='hidden', name='revision', value=revision),
                xhtml.textarea(page_contents, name='contents',
                               rows='48', cols='80'),
                xhtml.div(
                    xhtml.label('Describe your changes'),
                    xhtml.input(type='text', name='message',
                                value=default_message),
                ),
                xhtml.button('Save Changes', type='submit'),
                method='POST',
                action=flask.url_for('edit_submit', name=name),
            ),
        )

    @app.route('/', defaults={'name': 'FrontPage'})
    @app.route('/page/<name>', methods=['GET'])
    def page(name):
        revision = flask.request.args.get('revision', 'HEAD')
        try:
            header, body = gitki.render_page(name, revision=revision)
        except NotFoundError as e:
            header = 'New Page'
            body = xhtml.p(
                str(e),
                xhtml.span(' Do you want to ',
                           xhtml.a('create it?',
                                   href=flask.url_for('edit', name=name))))

        page_contents = xhtml.body(
            xhtml.h1(header, ' ',
                     xhtml.a('[edit]', href=flask.url_for('edit', name=name))),
            body)
        return page_contents

    return app
