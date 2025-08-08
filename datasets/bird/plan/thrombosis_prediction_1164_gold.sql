{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "MIN",
      "distinct": false,
      "ignoreNulls": false,
      "operands": [
        {
          "column": 0,
          "type": "DECIMAL"
        }
      ],
      "type": "DECIMAL",
      "name": "EXPR$0"
    }
  ],
  "id": "2",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "MINUS",
          "operator": "-",
          "type": "DECIMAL",
          "operands": [
            {
              "kind": "CAST",
              "operator": "CAST",
              "type": "DECIMAL",
              "operands": [
                {
                  "kind": "OTHER_FUNCTION",
                  "operator": "STRFTIME",
                  "type": "VARCHAR",
                  "operands": [
                    {
                      "kind": "LITERAL",
                      "value": "%Y",
                      "type": "CHAR",
                      "nullable": false,
                      "precision": 2
                    },
                    {
                      "kind": "CAST",
                      "operator": "CAST",
                      "type": "VARCHAR",
                      "operands": [
                        {
                          "kind": "CAST",
                          "operator": "CAST",
                          "type": "TIMESTAMP",
                          "operands": [
                            {
                              "kind": "INPUT_REF",
                              "index": 4,
                              "name": "$4",
                              "type": "DATE"
                            }
                          ]
                        }
                      ]
                    }
                  ]
                }
              ]
            },
            {
              "kind": "CAST",
              "operator": "CAST",
              "type": "DECIMAL",
              "operands": [
                {
                  "kind": "OTHER_FUNCTION",
                  "operator": "STRFTIME",
                  "type": "VARCHAR",
                  "operands": [
                    {
                      "kind": "LITERAL",
                      "value": "%Y",
                      "type": "CHAR",
                      "nullable": false,
                      "precision": 2
                    },
                    {
                      "kind": "CAST",
                      "operator": "CAST",
                      "type": "VARCHAR",
                      "operands": [
                        {
                          "kind": "CAST",
                          "operator": "CAST",
                          "type": "TIMESTAMP",
                          "operands": [
                            {
                              "kind": "INPUT_REF",
                              "index": 2,
                              "name": "$2",
                              "type": "DATE"
                            }
                          ]
                        }
                      ]
                    }
                  ]
                }
              ]
            }
          ]
        }
      ],
      "id": "1",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "Patient",
          "id": "0",
          "inputs": []
        }
      ]
    }
  ]
}