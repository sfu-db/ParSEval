{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "COUNT",
      "distinct": false,
      "ignoreNulls": false,
      "operands": [],
      "type": "BIGINT",
      "name": "EXPR$0"
    }
  ],
  "id": "6",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "LITERAL",
          "value": 0,
          "type": "INTEGER",
          "nullable": false,
          "precision": 10
        }
      ],
      "id": "5",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 0,
                "name": "$0",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "Dutch",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          },
          "variableset": "[]",
          "id": "4",
          "inputs": [
            {
              "relOp": "LogicalProject",
              "project": [
                {
                  "kind": "INPUT_REF",
                  "index": 0,
                  "name": "$0",
                  "type": "VARCHAR"
                }
              ],
              "id": "3",
              "inputs": [
                {
                  "relOp": "LogicalSort",
                  "sort": [
                    {
                      "column": 1,
                      "type": "INTEGER"
                    }
                  ],
                  "dir": [
                    "DESCENDING"
                  ],
                  "offset": 0,
                  "limit": 3,
                  "id": "2",
                  "inputs": [
                    {
                      "relOp": "LogicalProject",
                      "project": [
                        {
                          "kind": "INPUT_REF",
                          "index": 7,
                          "name": "$7",
                          "type": "VARCHAR"
                        },
                        {
                          "kind": "OTHER_FUNCTION",
                          "operator": "JULIANDAY",
                          "type": "INTEGER",
                          "operands": [
                            {
                              "kind": "INPUT_REF",
                              "index": 6,
                              "name": "$6",
                              "type": "DATE"
                            }
                          ]
                        }
                      ],
                      "id": "1",
                      "inputs": [
                        {
                          "relOp": "LogicalTableScan",
                          "table": "drivers",
                          "id": "0",
                          "inputs": []
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
  ]
}