import threading


_state = threading.local()


def set_current_tenant(tenant):
    _state.tenant = tenant


def get_current_tenant():
    return getattr(_state, 'tenant', None)


def clear_current_tenant():
    if hasattr(_state, 'tenant'):
        delattr(_state, 'tenant')

