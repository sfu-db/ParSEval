{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 1,
      "name": "$1",
      "type": "VARCHAR"
    }
  ],
  "id": "7",
  "inputs": [
    {
      "relOp": "LogicalJoin",
      "joinType": "inner",
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
            "kind": "INPUT_REF",
            "index": 2,
            "name": "$2",
            "type": "VARCHAR"
          }
        ]
      },
      "id": "6",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "molecule",
          "id": "0",
          "inputs": []
        },
        {
          "relOp": "LogicalSort",
          "sort": [
            {
              "column": 1,
              "type": "BIGINT"
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
              "relOp": "LogicalAggregate",
              "keys": [
                {
                  "column": 0,
                  "type": "VARCHAR"
                }
              ],
              "aggs": [
                {
                  "operator": "COUNT",
                  "distinct": false,
                  "ignoreNulls": false,
                  "operands": [
                    {
                      "column": 1,
                      "type": "VARCHAR"
                    }
                  ],
                  "type": "BIGINT",
                  "name": "EXPR$1"
                }
              ],
              "id": "4",
              "inputs": [
                {
                  "relOp": "LogicalProject",
                  "project": [
                    {
                      "kind": "INPUT_REF",
                      "index": 1,
                      "name": "$1",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "INPUT_REF",
                      "index": 2,
                      "name": "$2",
                      "type": "VARCHAR"
                    }
                  ],
                  "id": "3",
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
                            "index": 2,
                            "name": "$2",
                            "type": "VARCHAR"
                          },
                          {
                            "kind": "LITERAL",
                            "value": "=",
                            "type": "VARCHAR",
                            "nullable": false,
                            "precision": -1
                          }
                        ]
                      },
                      "variableset": "[]",
                      "id": "2",
                      "inputs": [
                        {
                          "relOp": "LogicalTableScan",
                          "table": "bond",
                          "id": "1",
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