"""WordPress payload preparation only. No network transport is installed here."""


class DeploymentDisabledError(RuntimeError):
    pass


def publish_draft(*args, **kwargs):
    # A future publisher must be implemented explicitly; neither credentials,
    # request flags nor environment variables can enable remote writes today.
    raise DeploymentDisabledError("Envío a WordPress deshabilitado: solo preparación local.")
