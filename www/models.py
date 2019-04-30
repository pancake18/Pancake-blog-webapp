import time, uuid

import orm
from orm import Model, StringField, BooleanField, FloatField, TextField
# import asyncio

# 生成唯一主键id
def next_id():
    return '%015d%s000' % (int(time.time() * 1000), uuid.uuid4().hex)  #uuid.uuid4().hex --> 将'-'删除

# 在编写ORM时，给一个Field增加一个default参数可以让ORM自己填入缺省值，非常方便。
# 并且，缺省值可以作为函数对象传入，在调用save()时自动计算。例如，主键id的缺省值
# 是函数next_id，创建时间created_at的缺省值是函数time.time，可以自动设置当前日期和时间。
# 一个User实例创建（不提供id和created_at的情况下）的时候id和created_at还没有数据，当对
# 该实例调用方法如save()的时候，会调用next.id()和time.time()自动生成这两个数据并存入数据库
# 日期和时间用float类型存储在数据库中，而不是datetime类型，这么做的好处是
# 不必关心数据库的时区以及时区转换问题，排序非常简单，显示的时候，只需要
# 做一个float到str的转换，也非常容易。

# 把网站需要的三个表（users, blogs, comments）用Model表示出来。

class User(Model):

    __table__ = 'users'

    id = StringField(primary_key=True, default=next_id, ddl='varchar(50)')
    email = StringField(ddl='varchar(50)')
    passwd = StringField(ddl='varchar(50)')
    admin = BooleanField()  # 是否为管理员
    name = StringField(ddl='varchar(50)')
    image = StringField(ddl='varchar(500)')
    created_at = FloatField(default=time.time)

class Blog(Model):

    __table__ = 'blogs'

    id = StringField(primary_key=True, default=next_id, ddl='varchar(50)')
    user_id = StringField(ddl='varchar(50)')
    user_name = StringField(ddl='varchar(50)')
    user_image = StringField(ddl='varchar(500)')
    name = StringField(ddl='varchar(50)')  # 博客名
    summary = StringField(ddl='varchar(200)')  # 博客概要
    content = TextField()  # 正文
    created_at = FloatField(default=time.time)

class Comment(Model):

    __table__ = 'comments'

    id = StringField(primary_key=True, default=next_id, ddl='varchar(50)')
    blog_id = StringField(ddl='varchar(50)')
    user_id = StringField(ddl='varchar(50)')
    user_name = StringField(ddl='varchar(50)')
    user_image = StringField(ddl='varchar(500)')
    content = TextField()  # 评论内容
    created_at = FloatField(default=time.time)

# 测试数据库操作
# if __name__ == '__main__':
#     async def myTest(loop):
#         u = User(email = '666666@qq.com', name = 'TEST', passwd = '666666', image = 'about:blank')
#         await orm.create_pool(loop = loop, user = 'www-data', password = 'www-data', db = 'awesome')
#         await u.save()
#         b = await User.findAll()
#         print(b)
#
#     loop = asyncio.get_event_loop()
#     loop.run_until_complete(myTest(loop))







