import re, time, json, logging, hashlib, base64, asyncio
import markdown  # markdown 处理日志文本的一种格式语法
from aiohttp import web

from coroweb import get, post
from apis import Page, APIError, APIValueError, APIResourceNotFoundError, APIPermissionError
from models import User, Comment, Blog, next_id
from config import configs

COOKIE_NAME = 'awesession'  # cookie名
_COOKIE_KEY = configs.session.secret  # cookie密令：用于加密cookie

# 一系列Web API 和 url处理函数，url处理函数会被add_routes筛选出来（需要具备__method__和__route__，即被@get或@post装饰的函数）进行注册
# 一个url处理函数过程：被get装饰，带上__method__和__route__
# 被RequestHandler进行封装，然后被注册
# 被注册的是一个RequestHandler对象，绑定着该url处理函数，该对象具备__call__(request)方法
# 对象调用的时候传入request，能自动从request提取出需要的参数传入绑定的url处理函数运行并返回结果
# return的结果要会过middlewares里面的response_factory(app, handler)进行转换变成正确的response
# app会根据http请求的方法和路径，从注册了的url处理函数中筛选出符合的，传入request然后执行过程中request会经过middlewares加工，最后返回正确的response

# 以下是所有网站所需要的后端API,和前端页面：
# 将数据处理逻辑（后端API）与页面分开（管理员页面，用户浏览页面）
# 后端API：
#     获取日志：GET /api/blogs
#     创建日志：POST /api/blogs
#     修改日志：POST /api/blogs/:blog_id
#     删除日志：POST /api/blogs/:blog_id/delete
#     获取评论：GET /api/comments
#     创建评论：POST /api/blogs/:blog_id/comments
#     删除评论：POST /api/comments/:comment_id/delete
#     创建新用户：POST /api/users
#     获取用户：GET /api/users

# 管理员页面包括：
#     评论列表管理页：GET /manage/comments
#     日志列表管理页：GET /manage/blogs
#     创建日志管理页：GET /manage/blogs/create
#     修改日志管理页：GET /manage/blogs/
#     用户列表管理页：GET /manage/users

# 用户浏览页面包括：
#     注册页：GET /register
#     登录页：GET /signin
#     注销页：GET /signout
#     首页：GET /
#     日志详情页：GET /blog/:blog_id

########################################################################################################################

# 查看是否是管理员用户
def check_admin(request):
    if request.__user__ is None or not request.__user__.admin:
        raise APIPermissionError()

# 获取页码信息：将传入的字符串页码转化为int类型
def get_page_index(page_str):
    p = 1
    try:
        p = int(page_str)
    except ValueError as e:
        pass
    if p < 1:
        p = 1
    return p

# 计算返回给客户端的加密cookie：传入一个当前登录用户user和max_age（用来计算出失效时间），返回一个加密好的cookie字符串
def user2cookie(user, max_age):
    # build cookie string by: id-expires-sha1，通过id，失效时间，sha1摘要算法创建cookie字符串
    # expires：失效时间
    expires = str(int(time.time() + max_age))
    s = '%s-%s-%s-%s' % (user.id, user.passwd, expires, _COOKIE_KEY)  # s字符串：user.id,, user.passwd, expires, _COOKIE_KEY
    # L包含三个元素：user.id, expires, 经过sha1摘要算法加密的s字符串，互相之间用'-'连接起来变成一个字符串并返回
    L = [user.id, expires, hashlib.sha1(s.encode('utf-8')).hexdigest()]
    return '-'.join(L)

# 解密cookie：传入一个cookie字符串进行验证，cookie验证通过，返回该user信息，验证失败返回None
async def cookie2user(cookie_str):  # 传入一个cookie字符串
    if not cookie_str:
        return None
    try:
        L = cookie_str.split('-')  # 将字符串去除'-'变成三个部分（user.id, expires, 经过sha1处理的s字符串）
        if len(L) != 3:
            return None
        uid, expires, sha1 = L  # 提取L中三个部分
        if int(expires) < time.time():  # cookie已失效
            return None
        user = await User.find(uid)  # 查找该用户id
        if user is None:
            return None
        s = '%s-%s-%s-%s' % (uid, user.passwd, expires, _COOKIE_KEY)  # 构造当前s字符串：user.id,, user.passwd, expires, _COOKIE_KEY
        if sha1 != hashlib.sha1(s.encode('utf-8')).hexdigest():  # 若传入的s字符串与当前构造的s字符串不相等
            logging.info('invalid sha1')
            return None
        user.passwd = '******'
        return user  # 验证通过，返回该user信息
    except Exception as e:
        logging.exception(e)
        return None

# 文本转HTML
def text2html(text):
    lines = map(lambda s: '<p>%s</p>' % s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'), filter(lambda s: s.strip() != '', text.split('\n')))
    return ''.join(lines)

########################################################################################################################

# 后端API --> 用户登录验证API
@post('/api/authenticate')
async def authenticate(*, email, passwd):  # 传入用户输入的email和passwd进行验证
    if not email:
        raise APIValueError('email', 'Invalid Email | 邮箱地址无效')
    if not passwd:
        raise APIValueError('passwd', 'Invalid Password | 密码无效')
    users = await User.findAll('email=?', [email])  # 通过email获取对应用户信息
    if len(users) == 0:
        raise APIValueError('email', 'Email Not Exist | 邮箱地址不存在')
    user = users[0]  # 取第一条记录（一般也只有一条记录：email的设置了unique key）
    # check passwd：
    # 密码是利用(id:passwd)通过SHA1摘要算法转换加密后存在数据库的
    sha1 = hashlib.sha1()
    sha1.update(user.id.encode('utf-8'))
    sha1.update(b':')
    sha1.update(passwd.encode('utf-8'))
    if user.passwd != sha1.hexdigest():
        raise APIValueError('passwd', 'Invalid Password | 密码错误')
    # authenticate ok, set cookie:认证通过，设置cookie传给客户端
    r = web.Response()  # 创建web.Response对象r
    r.set_cookie(COOKIE_NAME, user2cookie(user, 86400), max_age=86400, httponly=True)  # 传入所需参数设置cookie到r
    user.passwd = '******'
    r.content_type = 'application/json'  # 指定r的content_type
    r.body = json.dumps(user, ensure_ascii=False).encode('utf-8')  # 将user进行json序列化并放入r的body
    return r

# 后端API --> 获取评论信息API
@get('/api/comments')
async def api_comments(*, page='1'):
    page_index = get_page_index(page)
    num = await Comment.findNumber('count(id)')  # 查询评论数量
    p = Page(num, page_index)  # 生成分页对象p
    if num == 0:
        return dict(page=p, comments=())
    comments = await Comment.findAll(orderBy='created_at desc', limit=(p.offset, p.limit))
    return dict(page=p, comments=comments)

# 后端API --> 用户发表评论API
@post('/api/blogs/{id}/comments')
async def api_create_comment(id, request, *, content):  # 传入日志id，request，评论内容
    user = request.__user__  # 获取经过auth_factory加工的request的__user__属性
    if user is None:  # user为空说明没有登录或没有成功登录
        raise APIPermissionError('Please Signin First! | 请先登录！')
    if not content or not content.strip():  # 去掉首尾空格
        raise APIValueError('content')
    blog = await Blog.find(id)
    if blog is None:
        raise APIResourceNotFoundError('Blog')
    comment = Comment(blog_id=blog.id, user_id=user.id, user_name=user.name, user_image=user.image, content=content.strip())
    await comment.save()
    return comment

# 后端API --> 管理员删除评论API
@post('/api/comments/{id}/delete')
async def api_delete_comments(id, request):  # 传入评论id和request
    check_admin(request)  # 检查是否为管理员用户
    c = await Comment.find(id)  # 根据id查找该评论
    if c is None:
        raise APIResourceNotFoundError('Comment')
    await c.remove()
    return dict(id=id)

# 后端API --> 获取注册用户信息API
@get('/api/users')
async def api_get_users(*, page='1'):  # 传入page表示要获取第几页的已注册用户信息
    page_index = get_page_index(page)
    num = await User.findNumber('count(id)')  # 计算出一共有多少注册用户
    p = Page(num, page_index)  # 创建分页对象，不传入page_size则默认page_size=6，即一页显示6条记录
    if num == 0:
        return dict(page=p, users=())
    # 筛选出users表中对应位置的记录，并按找注册时间（最新的在前）排序
    users = await User.findAll(orderBy='created_at desc', limit=(p.offset, p.limit))
    for u in users:
        u.passwd = '******'  # 密码置为*
    return dict(page=p, users=users)

# 定义EMAIL和HASH的格式规范(正则表达式)
_RE_EMAIL = re.compile(r'^[a-z0-9\.\-\_]+\@[a-z0-9\-\_]+(\.[a-z0-9\-\_]+){1,4}$')
_RE_SHA1 = re.compile(r'^[0-9a-f]{40}$')

# 后端API --> 用户注册API
@post('/api/users')
async def api_register_user(*, email, name, passwd):  # 传入用户输入的email，name，passwd
    if not name or not name.strip():
        raise APIValueError('name')
    if not email or not _RE_EMAIL.match(email):
        raise APIValueError('email')
    if not passwd or not _RE_SHA1.match(passwd):
        raise APIValueError('passwd')
    users = await User.findAll('email=?', [email])
    if len(users) > 0:  # email已经注册过
        raise APIError('register:failed', 'email', 'Email is already in use.')
    uid = next_id()  # 生成用户id
    sha1_passwd = '%s:%s' % (uid, passwd)
    # 生成该用户信息
    user = User(id=uid, name=name.strip(), email=email, passwd=hashlib.sha1(sha1_passwd.encode('utf-8')).hexdigest(), image='http://www.gravatar.com/avatar/%s?d=mm&s=120' % hashlib.md5(email.encode('utf-8')).hexdigest())
    # 存入数据库
    await user.save()
    # make session cookie:生成cookie传给客户端
    r = web.Response()
    r.set_cookie(COOKIE_NAME, user2cookie(user, 86400), max_age=86400, httponly=True)
    user.passwd = '******'
    r.content_type = 'application/json'
    r.body = json.dumps(user, ensure_ascii=False).encode('utf-8')
    return r

# 后端API --> 获取日志列表API
@get('/api/blogs')
async def api_blogs(*, page='1'):
    page_index = get_page_index(page)
    num = await Blog.findNumber('count(id)')
    p = Page(num, page_index)
    if num == 0:
        return dict(page=p, blogs=())
    blogs = await Blog.findAll(orderBy='created_at desc', limit=(p.offset, p.limit))
    return dict(page=p, blogs=blogs)

# 后端API --> 获取日志详情API
@get('/api/blogs/{id}')
async def api_get_blog(*, id):
    blog = await Blog.find(id)
    return blog

# 后端API --> 发表日志API
@post('/api/blogs')
async def api_create_blog(request, *, name, summary, content):  # 传入request、日志名、日志摘要、日志内容
    check_admin(request)  # 检查是否为管理员
    if not name or not name.strip():
        raise APIValueError('name', 'name cannot be empty.')
    if not summary or not summary.strip():
        raise APIValueError('summary', 'summary cannot be empty.')
    if not content or not content.strip():
        raise APIValueError('content', 'content cannot be empty.')
    # 生成新的日志信息
    blog = Blog(user_id=request.__user__.id, user_name=request.__user__.name, user_image=request.__user__.image, name=name.strip(), summary=summary.strip(), content=content.strip())
    await blog.save()
    return blog

# 后端API --> 编辑日志API
@post('/api/blogs/{id}')
async def api_update_blog(id, request, *, name, summary, content):
    check_admin(request)
    blog = await Blog.find(id)
    if not name or not name.strip():
        raise APIValueError('name', 'name cannot be empty.')
    if not summary or not summary.strip():
        raise APIValueError('summary', 'summary cannot be empty.')
    if not content or not content.strip():
        raise APIValueError('content', 'content cannot be empty.')
    blog.name = name.strip()
    blog.summary = summary.strip()
    blog.content = content.strip()
    await blog.update()
    return blog

# 后端API --> 删除日志API
@post('/api/blogs/{id}/delete')
async def api_delete_blog(request, *, id):
    check_admin(request)
    blog = await Blog.find(id)
    await blog.remove()
    return dict(id=id)

# 后端API --> 删除用户API
@post('/api/users/{id}/delete')
async def api_delete_users(id, request):
    check_admin(request)
    id_buff = id
    user = await User.find(id)
    if user is None:
        raise APIResourceNotFoundError('User')
    await user.remove()
    # 给被删除的用户在评论中标记该用户已被删除
    comments = await Comment.findAll('user_id=?',[id])  # 查找该用户发表过的评论
    if comments:
        for comment in comments:
            id = comment.id
            c = await Comment.find(id)
            c.user_name = c.user_name + '(该用户已被删除)'
            await c.update()
    id = id_buff
    return dict(id=id)


########################################################################################################################

# 用户浏览页面 --> 网站首页
@get('/')
async def index(*, page='1'):  # 传入page表示要获取第几页的blog信息
    page_index = get_page_index(page)
    num = await Blog.findNumber('count(id)')  # 计算出一共有多少博客
    p = Page(num, page_index)  # 创建分页对象，不传入page_size则默认page_size=6，即一页显示6条记录
    if num == 0:
        blogs = []
    else:
        # 筛选出blogs表中对应位置的记录，并按找注册时间（最新的在前）排序
        blogs = await Blog.findAll(orderBy='created_at desc', limit=(p.offset, p.limit))
    return {
        '__template__': 'blogs.html',
        'page': p,
        'blogs': blogs
    }

# 用户浏览页面 --> 日志详情页面
@get('/blog/{id}')
async def get_blog(id):
    blog = await Blog.find(id)
    comments = await Comment.findAll('blog_id=?', [id], orderBy='created_at desc')
    for c in comments:
        c.html_content = markdown.markdown(c.content)
    blog.html_content = markdown.markdown(blog.content)
    return {
        '__template__': 'blog.html',
        'blog': blog,
        'comments': comments
    }

# 用户浏览页面 --> 注册页面
@get('/register')
def register():
    return {
       '__template__': 'register.html'
    }

# 用户浏览页面 --> 登录页面
@get('/signin')
def signin():
    return {
        '__template__': 'signin.html'
    }

# 用户浏览页面 --> 注销页面
@get('/signout')
def signout(request):
    referer = request.headers.get('Referer')  # 从request.headers得到'Referer'（引用页）
    r = web.HTTPFound(referer or '/')  # 有引用页则转至引用页，否则转到首页
    r.set_cookie(COOKIE_NAME, '-deleted-', max_age=0, httponly=True)  # 设置cookie
    logging.info('user signed out.')
    return r

########################################################################################################################

# 管理员页面 --> 管理员页面首页：先经过auth_factory验证该用户是否为管理员，验证通过则重定向到日志管理页面，否则跳转至登录页面
@get('/manage/')
def manage():
    return 'redirect:/manage/blogs'  # 重定向到日志管理页面

# 管理员页面 --> 评论管理页面
@get('/manage/comments')
def manage_comments(*, page='1'):  # 传入page表示要获取第几页的评论信息
    return {
        '__template__': 'manage_comments.html',
        'page_index': get_page_index(page)
    }

#  管理员页面 --> 日志管理页面
@get('/manage/blogs')
def manage_blogs(*, page='1'):  # 传入page表示要获取第几页的日志信息
    return {
        '__template__': 'manage_blogs.html',
        'page_index': get_page_index(page)
    }

# 管理员页面 --> 创建日志页面
@get('/manage/blogs/create')
def manage_create_blog():
    return {
        '__template__': 'manage_blog_edit.html',
        'id': '',
        'action': '/api/blogs'
    }

# 管理员页面 --> 编辑日志页面
@get('/manage/blogs/edit')
def manage_edit_blog(*, id):  # id：日志id
    return {
        '__template__': 'manage_blog_edit.html',
        'id': id,
        'action': '/api/blogs/%s' % id
    }

# 管理员页面 --> 用户管理页面
@get('/manage/users')
def manage_users(*, page='1'):
    return {
        '__template__': 'manage_users.html',
        'page_index': get_page_index(page)
    }

########################################################################################################################