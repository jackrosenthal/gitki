import ansi2html
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


def git_log(index, revision='HEAD', path=None, numstat=False, format='%H%n'):
    args = ['git', '-C', index, 'log', '--format=tformat:{}'.format(format)]
    if numstat:
        args.append('--numstat')
    args.append(revision)
    if path:
        args.append('--')
        args.append(path)
    result = subprocess.run(args, check=True, stdout=subprocess.PIPE,
                            encoding='utf-8', errors='replace')
    return result.stdout


def make_table(rows, headers=None):
    fmt_rows = []
    if headers:
        fmt_rows.append(xhtml.tr(*map(xhtml.th, headers)))
    for row in rows:
        fmt_rows.append(xhtml.tr(*map(xhtml.td, row)))
    return xhtml.table(*fmt_rows)


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
                                                 revision=revision)

        return name, gitkitext.to_html(gitkitext.parse(page_raw))

    def history(self, name, revision='HEAD'):
        log = iter(git_log(self.repo, revision=revision, path=name,
                           format='%H%n%cr%n%aN <%aE>%n%s', numstat=True)
                   .splitlines())
        while True:
            try:
                rev = next(log).rstrip()
            except StopIteration:
                return
            time = next(log).rstrip()
            author = next(log).rstrip()
            subject = next(log).rstrip()
            blank = next(log)
            insertions, deletions, *files = next(log).split()

            yield rev, time, author, subject, int(insertions), int(deletions)

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

    def template(self, title, body):
        return xhtml.html(
            xhtml.head(
                xhtml.title(xhtml(title)),
            ),
            xhtml.body(
                xhtml.h1(xhtml(title)),
                xhtml.div(body),
                xhtml.footer('Powered by Gitki'),
            ),
        )

def build_app(config):
    app = flask.Flask(__name__)
    app.config.from_mapping(config)

    gitki = Gitki(app.config['GITKI_HOME'])

    @app.route('/page/<name>/edit', methods=['POST'])
    def edit_submit(name):
        author = ('Alyssa P. Hacker', 'aphacker@example.org')
        contents = gitkitext.reformat(flask.request.form.get('contents'))
        gitki.update_file('{}.txt'.format(name),
                          author, contents,
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

        return gitki.template(
            'Editing {}'.format(name),
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

        return gitki.template(
            header,
            xhtml.div(
                xhtml.div(
                    xhtml.a('[edit]',
                            href=flask.url_for('edit', name=name)),
                    xhtml.a('[history]',
                            href=flask.url_for('history', name=name)),
                ),
                body))

    @app.route('/diff/<rev>', methods=['GET'])
    def diff(rev):
        try:
            result = subprocess.run(
                ['git', '-C', gitki.repo, 'show', '--color=always', rev],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
                encoding='utf-8', errors='replace')
        except subprocess.CalledProcessError:
            flask.abort(404, 'Unknown revision.')
        converter = ansi2html.Ansi2HTMLConverter(
            dark_bg=False,
            inline=True,
            scheme='osx',
        )
        html = converter.convert(result.stdout)
        # ugly ... :(
        html = html.replace('#AAAAAA', '#FFFFFF', 1)
        return gitki.template('Diff Output', html)

    @app.route('/page/<name>/history', methods=['GET'])
    def history(name):
        rows = []
        for revision, time, author, subject, cins, cdel in gitki.history(
                '{}.txt'.format(name)):
            changes = '+{} -{}'.format(cins, cdel)
            rows.append([
                xhtml.span(
                    xhtml.a(
                        revision[:6],
                        href=(flask.url_for('page', name=name)
                              + '?revision={}'.format(revision))),
                    ' ',
                    xhtml.a('[diff]', href=flask.url_for('diff', rev=revision)),
                ),
                xhtml(time),
                xhtml(author),
                xhtml(subject),
                xhtml(changes),
            ])

        return gitki.template(
            '{} History'.format(name),
            xhtml.div(
                xhtml.div(
                    xhtml.a(
                        '[Back to page]',
                        href=flask.url_for('page', name=name),
                    ),
                ),
                make_table(
                    rows,
                    headers=('Revision', 'Commit Time', 'Author',
                             'Description', 'Delta')),
            ),
        )

    return app
