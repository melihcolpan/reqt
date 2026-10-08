# reqt is now reqstorm

reqt has been renamed to **[reqstorm](https://pypi.org/project/reqstorm/)**, because another project already uses the reqt name.

```console
$ python -m pip install reqstorm
```

Then replace `import reqt` with `import reqstorm`. Everything else is the same.

This `reqt` package only installs reqstorm and re-exports it with a deprecation warning, so existing code keeps working. It will not get further updates.

Documentation: [reqstorm.github.io](https://reqstorm.github.io)
