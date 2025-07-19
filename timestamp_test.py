import datetime
import time
import calendar

class MyDateTime(datetime.datetime):
    @property
    def timestamp(self):
        try:
            # call original method if it exists
            return super().timestamp()
        except AttributeError:
            if self.tzinfo is None:
                return time.mktime(self.timetuple())
            else:
                return calendar.timegm(self.utctimetuple())

    def timestamp(self):
        # keep the method callable if desired
        return self.__class__.timestamp.fget(self)

# Create an instance of MyDateTime
expiry = MyDateTime(2025, 7, 16, 15, 30, 0)

print("expiry.timestamp =", expiry.timestamp)
print("expiry.timestamp() =", expiry.timestamp())
