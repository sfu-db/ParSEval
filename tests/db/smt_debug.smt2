(declare-fun T0.cds () String)
(assert (and (distinct T0.cds "1H0rCfylQp")))
(assert (str.in_re T0.cds (re.+ (re.range " " "~"))))
(assert (> (str.len T0.cds) 0))
(assert (distinct (str.substr T0.cds 0 1) " "))

sat
[ValueAssignment(column='T0.cds', alias='', value='A', data_type='', metadata={})]


(declare-fun T1.CDSCode () String)
(declare-fun T0.cds () String)
(assert (and (distinct T1.CDSCode "1H0rCfylQp")))
(assert (str.in_re T0.cds (re.+ (re.range " " "~"))))
(assert (> (str.len T0.cds) 0))
(assert (distinct (str.substr T0.cds 0 1) " "))
(assert (str.in_re T1.CDSCode (re.+ (re.range " " "~"))))
(assert (> (str.len T1.CDSCode) 0))
(assert (distinct (str.substr T1.CDSCode 0 1) " "))

sat
[ValueAssignment(column='T1.CDSCode', alias='', value='A', data_type='', metadata={})]


(declare-fun T0.AvgScrMath () Int)
(assert (< 560 T0.AvgScrMath))

sat
[ValueAssignment(column='T0.AvgScrMath', alias='', value=561, data_type='', metadata={})]


(declare-fun T1.CDSCode () String)
(declare-fun T0.cds () String)
(assert (and (distinct T1.CDSCode "1H0rCfylQp") (distinct T1.CDSCode "A")))
(assert (= T0.cds T1.CDSCode))
(assert (and (distinct T0.cds "1H0rCfylQp") (distinct T0.cds "A")))
(assert (= T0.cds T1.CDSCode))
(assert (str.in_re T1.CDSCode (re.+ (re.range " " "~"))))
(assert (> (str.len T1.CDSCode) 0))
(assert (distinct (str.substr T1.CDSCode 0 1) " "))
(assert (str.in_re T0.cds (re.+ (re.range " " "~"))))
(assert (> (str.len T0.cds) 0))
(assert (distinct (str.substr T0.cds 0 1) " "))

sat
[ValueAssignment(column='T1.CDSCode', alias='', value='?', data_type='', metadata={}), ValueAssignment(column='T0.cds', alias='', value='?', data_type='', metadata={})]


(declare-fun |T1.Charter Funding Type| () String)
(assert (distinct |T1.Charter Funding Type| "Directly funded"))
(assert (str.in_re |T1.Charter Funding Type| (re.+ (re.range " " "~"))))
(assert (> (str.len |T1.Charter Funding Type|) 0))
(assert (distinct (str.substr |T1.Charter Funding Type| 0 1) " "))

sat
[ValueAssignment(column='T1.Charter Funding Type', alias='', value='?', data_type='', metadata={})]


(declare-fun T0.AvgScrMath () Int)
(declare-fun |T1.Charter Funding Type| () String)
(assert (<= 560 T0.AvgScrMath))
(assert (str.in_re |T1.Charter Funding Type| (re.+ (re.range " " "~"))))
(assert (> (str.len |T1.Charter Funding Type|) 0))
(assert (distinct (str.substr |T1.Charter Funding Type| 0 1) " "))

sat
[ValueAssignment(column='T0.AvgScrMath', alias='', value=560, data_type='', metadata={})]


(declare-fun T1.CDSCode () String)
(declare-fun T0.cds () String)
(declare-fun |T1.Charter Funding Type| () String)
(assert (and (distinct T1.CDSCode "1H0rCfylQp")
     (distinct T1.CDSCode "A")
     (distinct T1.CDSCode "?")))
(assert (= T0.cds T1.CDSCode))
(assert (and (distinct T0.cds "1H0rCfylQp") (distinct T0.cds "A") (distinct T0.cds "?")))
(assert (= T0.cds T1.CDSCode))
(assert (str.in_re |T1.Charter Funding Type| (re.+ (re.range " " "~"))))
(assert (> (str.len |T1.Charter Funding Type|) 0))
(assert (distinct (str.substr |T1.Charter Funding Type| 0 1) " "))
(assert (str.in_re T1.CDSCode (re.+ (re.range " " "~"))))
(assert (> (str.len T1.CDSCode) 0))
(assert (distinct (str.substr T1.CDSCode 0 1) " "))
(assert (str.in_re T0.cds (re.+ (re.range " " "~"))))
(assert (> (str.len T0.cds) 0))
(assert (distinct (str.substr T0.cds 0 1) " "))

sat
[ValueAssignment(column='T1.CDSCode', alias='', value='!', data_type='', metadata={}), ValueAssignment(column='T0.cds', alias='', value='!', data_type='', metadata={})]


(declare-fun |T1.Charter Funding Type| () String)
(assert (= |T1.Charter Funding Type| "Directly funded"))
(assert (str.in_re |T1.Charter Funding Type| (re.+ (re.range " " "~"))))
(assert (> (str.len |T1.Charter Funding Type|) 0))
(assert (distinct (str.substr |T1.Charter Funding Type| 0 1) " "))

sat
[ValueAssignment(column='T1.Charter Funding Type', alias='', value='Directly funded', data_type='', metadata={})]


(declare-fun T0.AvgScrMath () Int)
(declare-fun |T1.Charter Funding Type| () String)
(assert (<= 560 T0.AvgScrMath))
(assert (str.in_re |T1.Charter Funding Type| (re.+ (re.range " " "~"))))
(assert (> (str.len |T1.Charter Funding Type|) 0))
(assert (distinct (str.substr |T1.Charter Funding Type| 0 1) " "))

sat
[ValueAssignment(column='T0.AvgScrMath', alias='', value=560, data_type='', metadata={})]


(declare-fun T1.CDSCode () String)
(declare-fun T0.cds () String)
(declare-fun |T1.Charter Funding Type| () String)
(assert (and (distinct T1.CDSCode "1H0rCfylQp")
     (distinct T1.CDSCode "A")
     (distinct T1.CDSCode "?")
     (distinct T1.CDSCode "!")))
(assert (= T0.cds T1.CDSCode))
(assert (and (distinct T0.cds "1H0rCfylQp")
     (distinct T0.cds "A")
     (distinct T0.cds "?")
     (distinct T0.cds "!")))
(assert (= T0.cds T1.CDSCode))
(assert (str.in_re |T1.Charter Funding Type| (re.+ (re.range " " "~"))))
(assert (> (str.len |T1.Charter Funding Type|) 0))
(assert (distinct (str.substr |T1.Charter Funding Type| 0 1) " "))
(assert (str.in_re T1.CDSCode (re.+ (re.range " " "~"))))
(assert (> (str.len T1.CDSCode) 0))
(assert (distinct (str.substr T1.CDSCode 0 1) " "))
(assert (str.in_re T0.cds (re.+ (re.range " " "~"))))
(assert (> (str.len T0.cds) 0))
(assert (distinct (str.substr T0.cds 0 1) " "))

sat
[ValueAssignment(column='T1.CDSCode', alias='', value='"', data_type='', metadata={}), ValueAssignment(column='T0.cds', alias='', value='"', data_type='', metadata={})]


(declare-fun |T1.Charter Funding Type| () String)
(assert (= |T1.Charter Funding Type| "Directly funded"))
(assert (str.in_re |T1.Charter Funding Type| (re.+ (re.range " " "~"))))
(assert (> (str.len |T1.Charter Funding Type|) 0))
(assert (distinct (str.substr |T1.Charter Funding Type| 0 1) " "))

sat
[ValueAssignment(column='T1.Charter Funding Type', alias='', value='Directly funded', data_type='', metadata={})]


(declare-fun T0.AvgScrMath () Int)
(declare-fun |T1.Charter Funding Type| () String)
(assert (<= 560 T0.AvgScrMath))
(assert (str.in_re |T1.Charter Funding Type| (re.+ (re.range " " "~"))))
(assert (> (str.len |T1.Charter Funding Type|) 0))
(assert (distinct (str.substr |T1.Charter Funding Type| 0 1) " "))

sat
[ValueAssignment(column='T0.AvgScrMath', alias='', value=560, data_type='', metadata={})]


(declare-fun T1.CDSCode () String)
(declare-fun T0.cds () String)
(declare-fun |T1.Charter Funding Type| () String)
(assert (and (distinct T1.CDSCode "1H0rCfylQp")
     (distinct T1.CDSCode "A")
     (distinct T1.CDSCode "?")
     (distinct T1.CDSCode "!")
     (distinct T1.CDSCode """")))
(assert (= T0.cds T1.CDSCode))
(assert (and (distinct T0.cds "1H0rCfylQp")
     (distinct T0.cds "A")
     (distinct T0.cds "?")
     (distinct T0.cds "!")
     (distinct T0.cds """")))
(assert (= T0.cds T1.CDSCode))
(assert (str.in_re |T1.Charter Funding Type| (re.+ (re.range " " "~"))))
(assert (> (str.len |T1.Charter Funding Type|) 0))
(assert (distinct (str.substr |T1.Charter Funding Type| 0 1) " "))
(assert (str.in_re T1.CDSCode (re.+ (re.range " " "~"))))
(assert (> (str.len T1.CDSCode) 0))
(assert (distinct (str.substr T1.CDSCode 0 1) " "))
(assert (str.in_re T0.cds (re.+ (re.range " " "~"))))
(assert (> (str.len T0.cds) 0))
(assert (distinct (str.substr T0.cds 0 1) " "))

sat
[ValueAssignment(column='T1.CDSCode', alias='', value='*', data_type='', metadata={}), ValueAssignment(column='T0.cds', alias='', value='*', data_type='', metadata={})]


