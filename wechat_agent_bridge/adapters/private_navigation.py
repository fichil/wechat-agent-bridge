"""One claimed semantic navigation, independent of read/send qualification."""
from ..console_models import ConsoleError


def navigate_once(port, checkpoint, claim):
    attempted = False
    try:
        checkpoint(); port.verify_source(); port.verify_row(); checkpoint()
        claim()
        checkpoint(); port.verify_source(); port.verify_row()
        attempted = True
        port.invoke()
        checkpoint()
        result = port.destination()
        if result.get('private_header') is not True or result.get('selected_row') is not True:
            raise ConsoleError('private_navigation_readback_missing')
        checkpoint()
        return dict(state='private_selected', attempted=True, navigation_verified=True,
                    identity_verified=False, send_verified=False, metadata=result)
    except Exception as exc:
        return dict(state='unknown' if attempted else 'failed', attempted=attempted,
                    navigation_verified=False, identity_verified=False, send_verified=False,
                    reason=str(exc) if isinstance(exc, ConsoleError) else 'private_navigation_failed')
