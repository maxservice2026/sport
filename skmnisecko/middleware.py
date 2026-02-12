class MobilePreviewMiddleware:
    """
    Persist mobile preview toggle in session.
    Use ?mobile=1 to enable and ?mobile=0 to disable.
    """

    TRUE_VALUES = {'1', 'true', 'yes', 'on'}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        mobile_param = request.GET.get('mobile')
        if mobile_param is not None:
            request.session['mobile_preview'] = mobile_param.lower() in self.TRUE_VALUES

        request.mobile_preview = bool(request.session.get('mobile_preview'))
        return self.get_response(request)
