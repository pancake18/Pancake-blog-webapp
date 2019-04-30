import logging; logging.basicConfig(level=logging.INFO)
import asyncio, os, json, time
from datetime import datetime
from aiohttp import web
from jinja2 import Environment, FileSystemLoader

import orm
from config import configs
from coroweb import add_routes, add_static
from handlers import cookie2user, COOKIE_NAME


# 初始化jinja2的函数,以便其他函数使用
def init_jinja2(app, **kw):
    logging.info('init jinja2...')
    options = dict(
        autoescape = kw.get('autoescape', True),
        block_start_string = kw.get('block_start_string', '{%'),
        block_end_string = kw.get('block_end_string', '%}'),
        variable_start_string = kw.get('variable_start_string', '{{'),
        variable_end_string = kw.get('variable_end_string', '}}'),
        auto_reload = kw.get('auto_reload', True)
    )
    path = kw.get('path', None)
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
    logging.info('set jinja2 template path: %s' % path)
    env = Environment(loader=FileSystemLoader(path), **options)
    filters = kw.get('filters', None)
    if filters is not None:
        for name, f in filters.items():
            env.filters[name] = f
    app['__templating__'] = env


# 以下是middleware,可以把通用的功能从每个URL处理函数中拿出来集中放到一个地方
# URL处理日志工厂（记录URL处理日志）
async def logger_factory(app, handler):
    async def logger(request):
        logging.info('Request: %s %s' % (request.method, request.path))
        return await handler(request)
    return logger

# 对于每个URL处理函数，如果我们都去写解析cookie的代码，那会导致代码重复很多次。
# 利用middle在处理URL之前，把cookie解析出来，并将登录用户绑定到request对象上，这样，后续的URL处理函数就可以直接拿到登录用户
# 认证处理工厂--把当前用户绑定到request上，并对URL/manage/进行拦截，检查当前用户是否是管理员身份
async def auth_factory(app, handler):
   async def auth(request):
       logging.info('check user: %s %s' % (request.method, request.path))
       request.__user__ = None
       cookie_str = request.cookies.get(COOKIE_NAME)
       if cookie_str:
           user = await cookie2user(cookie_str)
           if user:
               logging.info('set current user: %s' % user.email)
               request.__user__ = user  # 绑定uesr
       if request.path.startswith('/manage/') and (request.__user__ is None or not request.__user__.admin):
           return web.HTTPFound('/signin')
       return (await handler(request))
   return auth


# # 数据处理工厂
# async def data_factory(app, handler):
#     async def parse_data(request):
#         if request.method == 'POST':
#             if request.content_type.startswith('application/json'):
#                 request.__data__ = await request.json()
#                 logging.info('request json: %s' % str(request.__data__))
#             elif request.content_type.startswith('application/x-www-form-urlencoded'):
#                 request.__data__ = await request.post()
#                 logging.info('request form: %s' % str(request.__data__))
#         return (await handler(request))
#     return parse_data

# 响应转换处理工厂,将URL处理函数的返回值转化为web.Response对象
async def response_factory(app, handler):
    async def response(request):
        logging.info('Response handler...')
        r = await handler(request)  # 拿到url处理函数的返回值
        # 对返回值进行各种分析
        if isinstance(r, web.StreamResponse):  # 若r已经是一个StreamResponse对象，则直接返回r
            return r
        if isinstance(r, bytes):  # r是字节类型，则不用encode()
            resp = web.Response(body=r)
            resp.content_type = 'application/octet-stream'  # 二进制流数据
            return resp
        if isinstance(r, str):  # r是一个字符串
            if r.startswith('redirect:'):  # 重定向，转入别的网站
                return web.HTTPFound(r[9:])
            resp = web.Response(body=r.encode('utf-8'))  # str(unicode) ==> bytes(utf-8)
            # text/html：浏览器在获取到这种文件时会自动调用html的解析器对文件进行相应的处理。
            resp.content_type = 'text/html;charset=utf-8'
            return resp
        if isinstance(r, dict):  # 返回结果是一个dict，则需要使用模板处理或进行json序列化
            template = r.get('__template__')  # 得到对应模板名
            if template is None:  # 若无模板，则序列化为json
                resp = web.Response(body=json.dumps(r, ensure_ascii=False, default=lambda o: o.__dict__).encode('utf-8'))
                resp.content_type = 'application/json;charset=utf-8'
                return resp
            else:  #  否则使用jinja2模板
                r['__user__'] = request.__user__
                #  得到jinja2模板并传入参数
                resp = web.Response(body=app['__templating__'].get_template(template).render(**r).encode('utf-8'))
                resp.content_type = 'text/html;charset=utf-8'
                return resp
        if isinstance(r, int) and r >= 100 and r < 600:  # r是一个整数
            return web.Response(r)
        if isinstance(r, tuple) and len(r) == 2:  # r是一个包含两个元素的元组
            t, m = r
            if isinstance(t, int) and t >= 100 and t < 600:
                return web.Response(t, str(m))
        # default，错误
        resp = web.Response(body=str(r).encode('utf-8'))
        # text/plain：纯文本的形式，浏览器在获取到这种文件时并不会对其进行处理。
        resp.content_type = 'text/plain;charset=utf-8'
        return resp
    return response

# 时间转换（拦截器）
def datetime_filter(t):
    delta = int(time.time() - t)
    if delta < 60:
        return u'1分钟前'
    if delta < 3600:
        return u'%s分钟前' % (delta // 60)
    if delta < 86400:
        return u'%s小时前' % (delta // 3600)
    if delta < 604800:
        return u'%s天前' % (delta // 86400)
    dt = datetime.fromtimestamp(t)
    return u'%s年%s月%s日' % (dt.year, dt.month, dt.day)





async def init(loop):
    await orm.create_pool(loop=loop, **configs.db)  # 用配置文件的'db'信息创建数据库连接池

    # request被处理前会经过一系列的middlewares的加工
    app = web.Application(loop=loop, middlewares=[logger_factory, response_factory, auth_factory])  # 创建webapp

    init_jinja2(app, filters=dict(datetime=datetime_filter))  # 初始化jinja2

    add_routes(app, 'handlers')  # 批量注册handlers.py里面符合条件的url处理函数

    add_static(app)  # 注册静态文件夹

    srv = await loop.create_server(app.make_handler(), '127.0.0.1', 9000)  # 创建TCP服务
    logging.info('server started at http://127.0.0.1:9000...')

    return srv

if __name__ == '__main__':
    loop = asyncio.get_event_loop()  # 创建事件循环对象loop
    loop.run_until_complete(init(loop))
    loop.run_forever()