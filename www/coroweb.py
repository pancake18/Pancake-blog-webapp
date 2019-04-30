import asyncio, os, inspect, logging, functools
from urllib import parse
from aiohttp import web

from apis import APIError

# 装饰器：对URL处理函数进行装饰，让其带上URL信息。方法：__method__,路径：__route__
# 装饰器 @post(path)
def get(path):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kw):
            return func(*args, **kw)
        wrapper.__method__ = 'GET'
        wrapper.__route__ = path
        return wrapper
    return decorator

# 装饰器 @post(path)
def post(path):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kw):
            return func(*args, **kw)
        wrapper.__method__ = 'POST'
        wrapper.__route__ = path
        return wrapper
    return decorator

# 定义RequestHandler类需要的一些函数
# 得到fn需要的没有缺省值的命名关键字参数名
def get_required_kw_args(fn):
    args = []
    # signature方法，获取函数fn签名对象，通过函数签名的parameters属性，获取函数参数
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        # 如果参数为命名关键字参数且没有缺省值
        if param.kind == inspect.Parameter.KEYWORD_ONLY and param.default == inspect.Parameter.empty:
            args.append(name)
    return tuple(args)

# 得到fn需要的所有命名关键字参数名
def get_named_kw_args(fn):
    args = []
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.KEYWORD_ONLY:
            args.append(name)
    return tuple(args)

# 判断fn是否需要命名关键字参数
def has_named_kw_args(fn):
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.KEYWORD_ONLY:
            return True

# 判断fn是否需要关键字参数
def has_var_kw_arg(fn):
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            return True

# 判断fn是否需要'request'参数且该参数是否最后一个的命名关键字参数
def has_request_arg(fn):
    sig = inspect.signature(fn)
    params = sig.parameters
    found = False
    for name, param in params.items():
        if name == 'request':
            found = True
            continue
        # 找到'request'参数且后面的参数如果不是可变参数/命名关键字参数/关键字参数，抛出VauleError
        if found and (param.kind != inspect.Parameter.VAR_POSITIONAL and param.kind != inspect.Parameter.KEYWORD_ONLY and param.kind != inspect.Parameter.VAR_KEYWORD):
            raise ValueError('request parameter must be the last named parameter in function: %s%s' % (fn.__name__, str(sig)))
    return found

# 用RequestHandler来封装一个URL处理函数，分析URL处理函数需要接收的参数。
# 由于定义了__call__()方法，因此其实例是可以调用的，可以将其实例视为函数（对URL处理函数进行封装后的URL处理函数）。
# 从request中获取必要的参数，传给并调用URL函数，得到结果
# 要完全符合aiohttp框架的要求，就需要把结果转换为web.Response对象，是在创建middlewares的工厂函数实现。
class RequestHandler(object):

    def __init__(self, app, fn):
        self._app = app
        self._func = fn  # 将一个实例绑定一个URL处理函数
        # 定义一些属性表示fn需要哪些参数
        self._has_request_arg = has_request_arg(fn)
        self._has_var_kw_arg = has_var_kw_arg(fn)  # fn是否需要关键字参数
        self._has_named_kw_args = has_named_kw_args(fn)  # fn是否需要命名关键字参数
        self._named_kw_args = get_named_kw_args(fn)  # fn需要的所有命名关键字名的tuple
        self._required_kw_args = get_required_kw_args(fn)  # fn需要的没有缺省值的命名关键字名的tuple

    # 协程，传入一个request
    async def __call__(self, request):
        kw = None
        # 如果fn需要关键字参数，或需要命名关键字参数，或需要没有缺省值的命名关键字参数
        if self._has_var_kw_arg or self._has_named_kw_args or self._required_kw_args:
            if request.method == 'POST':  # 若请求方法为POST，则需要提取用户提交的数据
                if not request.content_type:  # 若Content-Type为空
                    return web.HTTPBadRequest(test='Missing Content-Type.')
                ct = request.content_type.lower()  # 得到request的content_type（指定body的数据格式）并转换成小写
                if ct.startswith('application/json'):  # 若Content-type为application/json
                    params = await request.json()  # 获取body中的json串
                    if not isinstance(params, dict):  #如果json不是一个dict
                        return web.HTTPBadRequest(text='JSON body must be object.')
                    kw = params  # 从request中获取参数
                # 若Content-Type为application/x-www-form-urlencoded或multipart/form-data
                elif ct.startswith('application/x-www-form-urlencoded') or ct.startswith('multipart/form-data'):
                    params = await request.post()
                    kw = dict(**params)  # 从request中获取参数
                else:  # 若Content-Type为其他类型，则不支持。
                    return web.HTTPBadRequest(text='Unsupported Content-Type: %s' % request.content_type)
            if request.method == 'GET':  # 若请求方法为GET
                qs = request.query_string  # 获取url中'?'后的值 --> xxx=xxx
                if qs:
                    kw = dict()
                    for k, v in parse.parse_qs(qs, True).items():  # 解析ps(把get请求的参数转化成字典)，提取参数
                        kw[k] = v[0]
        if kw is None:
            kw = dict(**request.match_info)
        else:
            if not self._has_var_kw_arg and self._named_kw_args:  # 如果url处理函数不需要关键字参数，而需要命名关键字参数
                copy = dict()
                for name in self._named_kw_args:  # 过滤kw，仅保留url处理函数需要的命名关键字参数
                    if name in kw:
                        copy[name] = kw[name]
                kw = copy
            for k, v in request.match_info.items():  # check named arg:检查命名关键字参数
                if k in kw:
                    logging.warning('Duplicate arg name in named arg and kw args: %s' % k)
                kw[k] = v
        if self._has_request_arg:  # 若url处理函数需要整个request
            kw['request'] = request
        # check required kw:
        if self._required_kw_args:  # 如果url处理函数需要没有缺省值的命名关键字参数，而request中没有提供相应数值
            for name in self._required_kw_args:
                if not name in kw:
                    return web.HTTPBadRequest(text='Missing argument: %s' % name)
        logging.info('call with args: %s' % str(kw))
        try:
            r = await self._func(**kw)  # 传kw给url处理函数并异步调用,返回结果
            return r
        except APIError as e:
            return dict(error=e.error, data=e.data, message=e.message)


# 编写一个add_route函数，用来注册一个URL处理函数，验证函数是否有包含URL的方法与路径信息，以及将函数变为协程。
def add_route(app, fn):
    # 获得函数的__method__和__route__
    method = getattr(fn, '__method__', None)
    path = getattr(fn, '__route__', None)
    if path is None or method is None:  # 该函数没有附带path或method
        raise ValueError('@get or @post not defined in %s.' % str(fn))
    # 判断fn是否为协程或生成器
    if not asyncio.iscoroutinefunction(fn) and not inspect.isgeneratorfunction(fn):
        # 将fn变为协程
        fn = asyncio.coroutine(fn)
    logging.info('add route %s %s => %s(%s)' % (method, path, fn.__name__, ', '.join(inspect.signature(fn).parameters.keys())))
    # 通过app.router.add_route注册fn
    app.router.add_route(method, path, RequestHandler(app, fn))


# 批量注册：定义add_routes函数，自动注册handler模块的所有符合条件的URL函数
def add_routes(app, module_name):
    n = module_name.rfind('.')
    if n == (-1):
        mod = __import__(module_name, globals(), locals())
    else:
        name = module_name[n+1:]
        mod = getattr(__import__(module_name[:n], globals(), locals(), [name]), name)
    for attr in dir(mod):
        if attr.startswith('_'):
            continue
        fn = getattr(mod, attr)
        if callable(fn):
            method = getattr(fn, '__method__', None)
            path = getattr(fn, '__route__', None)
            # 这里进行查询而不是等待add_route函数查询，因为在add_route查询有错误就会报错了
            if method and path:
                add_route(app, fn)

# 定义add_static函数，注册static文件夹下的文件
def add_static(app):
    # 得到当前文件夹中static的路径
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    # 注册静态文件夹
    app.router.add_static('/static/', path)
    logging.info('add static %s => %s' % ('/static/', path))



