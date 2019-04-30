import json, logging, inspect, functools
# 处理分页和API错误,诸如账号登录信息的错误

# 建立Page类来处理分页,可以在page_size更改每页项目的个数
class Page(object):

    def __init__(self, item_count, page_index=1, page_size=6):  # item_count：项目总数 page_index=1：页码 page_size=6：每页的项目数
        self.item_count = item_count
        self.page_size = page_size
        # 页面总数
        self.page_count = item_count // page_size + (1 if item_count % page_size > 0 else 0)
        if (item_count == 0) or (page_index > self.page_count):
            self.offset = 0
            self.limit = 0
            self.page_index = 1
        else:
            self.page_index = page_index
            self.offset = self.page_size * (page_index - 1)
            self.limit = self.page_size
        self.has_next = self.page_index < self.page_count
        self.has_previous = self.page_index > 1

    def __str__(self):
        return 'item_count: %s, page_count: %s, page_index: %s, page_size: %s, offset: %s, limit: %s' % (self.item_count, self.page_count, self.page_index, self.page_size, self.offset, self.limit)

    __repr__ = __str__

# 以下为API的几类错误代码
# APIError基类，包含错误类型（必要），数据（可选），信息（可选）
class APIError(Exception):

    def __init__(self, error, data='', message=''):
        super(APIError, self).__init__(message)
        self.error = error
        self.data = data
        self.message = message

# 输入数据错误或无效，data说明了问题字段
class APIValueError(APIError):

    def __init__(self, field, message=''):
        super(APIValueError, self).__init__('value:invalid', field, message)

# 找不到资源，data说明资源名字
class APIResourceNotFoundError(APIError):

    def __init__(self, field, message=''):
        super(APIResourceNotFoundError, self).__init__('value:notfound', field, message)

# 表面接口没有权限
class APIPermissionError(APIError):

    def __init__(self, message=''):
        super(APIPermissionError, self).__init__('permission:forbidden', 'permission', message)

if __name__=='__main__':
    import doctest
    doctest.testmod()