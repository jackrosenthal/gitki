import setuptools

setuptools.setup(
    name='gitki',
    version='0.1',

    description='Dead Simple Git-based Wiki',
    long_description='Collaborate on a wiki using Git',

    url='https://github.com/jackrosenthal/gitki',

    author='Jack Rosenthal',
    author_email='jrosenth@chromium.org',

    license='MIT',

    keywords='wiki',
    packages=setuptools.find_packages('gitki'),

    python_requires='>=3.6, <4',
    install_requires=[
        'ansi2html>=1.6',
        'flask>=1.1',
        'werkzeug>=1.0',
    ],
)
