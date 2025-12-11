(declare-fun T0.CDSCode () String)
(assert (and (distinct T0.CDSCode "kkXlhiB9Wu")))
(assert (str.in_re T0.CDSCode (re.+ (re.range " " "~"))))
(assert (> (str.len T0.CDSCode) 0))
(assert (distinct (str.substr T0.CDSCode 0 1) " "))

sat
[ValueAssignment(column='T0.CDSCode', alias='', value='A', data_type='', metadata={})]


(declare-fun T1.CDSCode () String)
(declare-fun T0.CDSCode () String)
(assert (and (distinct T1.CDSCode "kkXlhiB9Wu")))
(assert (str.in_re T0.CDSCode (re.+ (re.range " " "~"))))
(assert (> (str.len T0.CDSCode) 0))
(assert (distinct (str.substr T0.CDSCode 0 1) " "))
(assert (str.in_re T1.CDSCode (re.+ (re.range " " "~"))))
(assert (> (str.len T1.CDSCode) 0))
(assert (distinct (str.substr T1.CDSCode 0 1) " "))

sat
[ValueAssignment(column='T1.CDSCode', alias='', value='A', data_type='', metadata={})]



sat
[]


(declare-fun T1.CDSCode () String)
(declare-fun T0.CDSCode () String)
(assert (= T0.CDSCode T1.CDSCode))
(assert (and (distinct T1.CDSCode "kkXlhiB9Wu") (distinct T1.CDSCode "A")))
(assert (= T0.CDSCode T1.CDSCode))
(assert (and (distinct T0.CDSCode "kkXlhiB9Wu") (distinct T0.CDSCode "A")))
(assert (str.in_re T0.CDSCode (re.+ (re.range " " "~"))))
(assert (> (str.len T0.CDSCode) 0))
(assert (distinct (str.substr T0.CDSCode 0 1) " "))
(assert (str.in_re T1.CDSCode (re.+ (re.range " " "~"))))
(assert (> (str.len T1.CDSCode) 0))
(assert (distinct (str.substr T1.CDSCode 0 1) " "))

sat
[ValueAssignment(column='T0.CDSCode', alias='', value='@', data_type='', metadata={}), ValueAssignment(column='T1.CDSCode', alias='', value='@', data_type='', metadata={})]



sat
[]


(declare-fun T1.CDSCode () String)
(declare-fun T0.CDSCode () String)
(assert (= T0.CDSCode T1.CDSCode))
(assert (and (distinct T1.CDSCode "kkXlhiB9Wu")
     (distinct T1.CDSCode "A")
     (distinct T1.CDSCode "@")))
(assert (= T0.CDSCode T1.CDSCode))
(assert (and (distinct T0.CDSCode "kkXlhiB9Wu")
     (distinct T0.CDSCode "A")
     (distinct T0.CDSCode "@")))
(assert (str.in_re T0.CDSCode (re.+ (re.range " " "~"))))
(assert (> (str.len T0.CDSCode) 0))
(assert (distinct (str.substr T0.CDSCode 0 1) " "))
(assert (str.in_re T1.CDSCode (re.+ (re.range " " "~"))))
(assert (> (str.len T1.CDSCode) 0))
(assert (distinct (str.substr T1.CDSCode 0 1) " "))

sat
[ValueAssignment(column='T0.CDSCode', alias='', value='H', data_type='', metadata={}), ValueAssignment(column='T1.CDSCode', alias='', value='H', data_type='', metadata={})]



sat
[]


(declare-fun T1.CDSCode () String)
(declare-fun T0.CDSCode () String)
(assert (and (distinct T1.CDSCode "kkXlhiB9Wu")
     (distinct T1.CDSCode "A")
     (distinct T1.CDSCode "@")
     (distinct T1.CDSCode "H")))
(assert (= T0.CDSCode T1.CDSCode))
(assert (= T0.CDSCode T1.CDSCode))
(assert (and (distinct T0.CDSCode "kkXlhiB9Wu")
     (distinct T0.CDSCode "A")
     (distinct T0.CDSCode "@")
     (distinct T0.CDSCode "H")))
(assert (str.in_re T1.CDSCode (re.+ (re.range " " "~"))))
(assert (> (str.len T1.CDSCode) 0))
(assert (distinct (str.substr T1.CDSCode 0 1) " "))
(assert (str.in_re T0.CDSCode (re.+ (re.range " " "~"))))
(assert (> (str.len T0.CDSCode) 0))
(assert (distinct (str.substr T0.CDSCode 0 1) " "))

sat
[ValueAssignment(column='T1.CDSCode', alias='', value='O', data_type='', metadata={}), ValueAssignment(column='T0.CDSCode', alias='', value='O', data_type='', metadata={})]



sat
[]


(declare-fun T1.CDSCode () String)
(declare-fun T0.CDSCode () String)
(assert (and (distinct T1.CDSCode "kkXlhiB9Wu")
     (distinct T1.CDSCode "A")
     (distinct T1.CDSCode "@")
     (distinct T1.CDSCode "H")
     (distinct T1.CDSCode "O")))
(assert (= T0.CDSCode T1.CDSCode))
(assert (= T0.CDSCode T1.CDSCode))
(assert (and (distinct T0.CDSCode "kkXlhiB9Wu")
     (distinct T0.CDSCode "A")
     (distinct T0.CDSCode "@")
     (distinct T0.CDSCode "H")
     (distinct T0.CDSCode "O")))
(assert (str.in_re T1.CDSCode (re.+ (re.range " " "~"))))
(assert (> (str.len T1.CDSCode) 0))
(assert (distinct (str.substr T1.CDSCode 0 1) " "))
(assert (str.in_re T0.CDSCode (re.+ (re.range " " "~"))))
(assert (> (str.len T0.CDSCode) 0))
(assert (distinct (str.substr T0.CDSCode 0 1) " "))

sat
[ValueAssignment(column='T1.CDSCode', alias='', value='_', data_type='', metadata={}), ValueAssignment(column='T0.CDSCode', alias='', value='_', data_type='', metadata={})]



sat
[]


(declare-fun T1.CDSCode () String)
(declare-fun T0.CDSCode () String)
(assert (= T0.CDSCode T1.CDSCode))
(assert (and (distinct T1.CDSCode "kkXlhiB9Wu")
     (distinct T1.CDSCode "A")
     (distinct T1.CDSCode "@")
     (distinct T1.CDSCode "H")
     (distinct T1.CDSCode "O")
     (distinct T1.CDSCode "_")))
(assert (= T0.CDSCode T1.CDSCode))
(assert (and (distinct T0.CDSCode "kkXlhiB9Wu")
     (distinct T0.CDSCode "A")
     (distinct T0.CDSCode "@")
     (distinct T0.CDSCode "H")
     (distinct T0.CDSCode "O")
     (distinct T0.CDSCode "_")))
(assert (str.in_re T0.CDSCode (re.+ (re.range " " "~"))))
(assert (> (str.len T0.CDSCode) 0))
(assert (distinct (str.substr T0.CDSCode 0 1) " "))
(assert (str.in_re T1.CDSCode (re.+ (re.range " " "~"))))
(assert (> (str.len T1.CDSCode) 0))
(assert (distinct (str.substr T1.CDSCode 0 1) " "))

sat
[ValueAssignment(column='T0.CDSCode', alias='', value='W', data_type='', metadata={}), ValueAssignment(column='T1.CDSCode', alias='', value='W', data_type='', metadata={})]



sat
[]


(declare-fun T1.CDSCode () String)
(declare-fun T0.CDSCode () String)
(assert (= T0.CDSCode T1.CDSCode))
(assert (and (distinct T1.CDSCode "kkXlhiB9Wu")
     (distinct T1.CDSCode "A")
     (distinct T1.CDSCode "@")
     (distinct T1.CDSCode "H")
     (distinct T1.CDSCode "O")
     (distinct T1.CDSCode "_")
     (distinct T1.CDSCode "W")))
(assert (and (distinct T0.CDSCode "kkXlhiB9Wu")
     (distinct T0.CDSCode "A")
     (distinct T0.CDSCode "@")
     (distinct T0.CDSCode "H")
     (distinct T0.CDSCode "O")
     (distinct T0.CDSCode "_")
     (distinct T0.CDSCode "W")))
(assert (= T0.CDSCode T1.CDSCode))
(assert (str.in_re T0.CDSCode (re.+ (re.range " " "~"))))
(assert (> (str.len T0.CDSCode) 0))
(assert (distinct (str.substr T0.CDSCode 0 1) " "))
(assert (str.in_re T1.CDSCode (re.+ (re.range " " "~"))))
(assert (> (str.len T1.CDSCode) 0))
(assert (distinct (str.substr T1.CDSCode 0 1) " "))

sat
[ValueAssignment(column='T0.CDSCode', alias='', value='Q', data_type='', metadata={}), ValueAssignment(column='T1.CDSCode', alias='', value='Q', data_type='', metadata={})]


(declare-fun |T0.Enrollment (K-12)| () Real)
(assert (= 8.0 |T0.Enrollment (K-12)|))

sat
[ValueAssignment(column='T0.Enrollment (K-12)', alias='', value=8.0, data_type='', metadata={})]


(declare-fun T1.CDSCode () String)
(declare-fun T0.CDSCode () String)
(assert (and (distinct T1.CDSCode "kkXlhiB9Wu")
     (distinct T1.CDSCode "A")
     (distinct T1.CDSCode "@")
     (distinct T1.CDSCode "H")
     (distinct T1.CDSCode "O")
     (distinct T1.CDSCode "_")
     (distinct T1.CDSCode "W")
     (distinct T1.CDSCode "Q")))
(assert (= T0.CDSCode T1.CDSCode))
(assert (= T0.CDSCode T1.CDSCode))
(assert (and (distinct T0.CDSCode "kkXlhiB9Wu")
     (distinct T0.CDSCode "A")
     (distinct T0.CDSCode "@")
     (distinct T0.CDSCode "H")
     (distinct T0.CDSCode "O")
     (distinct T0.CDSCode "_")
     (distinct T0.CDSCode "W")
     (distinct T0.CDSCode "Q")))
(assert (str.in_re T1.CDSCode (re.+ (re.range " " "~"))))
(assert (> (str.len T1.CDSCode) 0))
(assert (distinct (str.substr T1.CDSCode 0 1) " "))
(assert (str.in_re T0.CDSCode (re.+ (re.range " " "~"))))
(assert (> (str.len T0.CDSCode) 0))
(assert (distinct (str.substr T0.CDSCode 0 1) " "))

sat
[ValueAssignment(column='T1.CDSCode', alias='', value='D', data_type='', metadata={}), ValueAssignment(column='T0.CDSCode', alias='', value='D', data_type='', metadata={})]


(declare-fun |T0.Enrollment (K-12)| () Real)
(assert (= 3.0 |T0.Enrollment (K-12)|))

sat
[ValueAssignment(column='T0.Enrollment (K-12)', alias='', value=3.0, data_type='', metadata={})]


(declare-fun T1.CDSCode () String)
(declare-fun T0.CDSCode () String)
(assert (and (distinct T1.CDSCode "kkXlhiB9Wu")
     (distinct T1.CDSCode "A")
     (distinct T1.CDSCode "@")
     (distinct T1.CDSCode "H")
     (distinct T1.CDSCode "O")
     (distinct T1.CDSCode "_")
     (distinct T1.CDSCode "W")
     (distinct T1.CDSCode "Q")
     (distinct T1.CDSCode "D")))
(assert (= T0.CDSCode T1.CDSCode))
(assert (= T0.CDSCode T1.CDSCode))
(assert (and (distinct T0.CDSCode "kkXlhiB9Wu")
     (distinct T0.CDSCode "A")
     (distinct T0.CDSCode "@")
     (distinct T0.CDSCode "H")
     (distinct T0.CDSCode "O")
     (distinct T0.CDSCode "_")
     (distinct T0.CDSCode "W")
     (distinct T0.CDSCode "Q")
     (distinct T0.CDSCode "D")))
(assert (str.in_re T1.CDSCode (re.+ (re.range " " "~"))))
(assert (> (str.len T1.CDSCode) 0))
(assert (distinct (str.substr T1.CDSCode 0 1) " "))
(assert (str.in_re T0.CDSCode (re.+ (re.range " " "~"))))
(assert (> (str.len T0.CDSCode) 0))
(assert (distinct (str.substr T0.CDSCode 0 1) " "))

sat
[ValueAssignment(column='T1.CDSCode', alias='', value='P', data_type='', metadata={}), ValueAssignment(column='T0.CDSCode', alias='', value='P', data_type='', metadata={})]


