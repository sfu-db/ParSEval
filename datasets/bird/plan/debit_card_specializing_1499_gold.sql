{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 0,
      "type": "FLOAT"
    }
  ],
  "dir": [
    "DESCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "5",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 1,
          "name": "$1",
          "type": "FLOAT"
        }
      ],
      "id": "4",
      "inputs": [
        {
          "relOp": "LogicalAggregate",
          "keys": [
            {
              "column": 0,
              "type": "VARCHAR"
            }
          ],
          "aggs": [
            {
              "operator": "SUM",
              "distinct": false,
              "ignoreNulls": false,
              "operands": [
                {
                  "column": 1,
                  "type": "FLOAT"
                }
              ],
              "type": "FLOAT",
              "name": "EXPR$0"
            }
          ],
          "id": "3",
          "inputs": [
            {
              "relOp": "LogicalProject",
              "project": [
                {
                  "kind": "OTHER_FUNCTION",
                  "operator": "SUBSTRING",
                  "type": "VARCHAR",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 1,
                      "name": "$1",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "LITERAL",
                      "value": 5,
                      "type": "INTEGER",
                      "nullable": false,
                      "precision": 10
                    },
                    {
                      "kind": "LITERAL",
                      "value": 2,
                      "type": "INTEGER",
                      "nullable": false,
                      "precision": 10
                    }
                  ]
                },
                {
                  "kind": "INPUT_REF",
                  "index": 2,
                  "name": "$2",
                  "type": "FLOAT"
                }
              ],
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalFilter",
                  "condition": {
                    "kind": "EQUALS",
                    "operator": "=",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "OTHER_FUNCTION",
                        "operator": "SUBSTRING",
                        "type": "VARCHAR",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 1,
                            "name": "$1",
                            "type": "VARCHAR"
                          },
                          {
                            "kind": "LITERAL",
                            "value": 1,
                            "type": "INTEGER",
                            "nullable": false,
                            "precision": 10
                          },
                          {
                            "kind": "LITERAL",
                            "value": 4,
                            "type": "INTEGER",
                            "nullable": false,
                            "precision": 10
                          }
                        ]
                      },
                      {
                        "kind": "LITERAL",
                        "value": "2012",
                        "type": "VARCHAR",
                        "nullable": false,
                        "precision": -1
                      }
                    ]
                  },
                  "variableset": "[]",
                  "id": "1",
                  "inputs": [
                    {
                      "relOp": "LogicalTableScan",
                      "table": "yearmonth",
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