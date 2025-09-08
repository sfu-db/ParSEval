{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 1,
      "type": "FLOAT"
    }
  ],
  "dir": [
    "ASCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "6",
  "inputs": [
    {
      "relOp": "LogicalAggregate",
      "keys": [
        {
          "column": 0,
          "type": "INTEGER"
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
          "name": "ASC"
        }
      ],
      "id": "5",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
            {
              "kind": "INPUT_REF",
              "index": 0,
              "name": "$0",
              "type": "INTEGER"
            },
            {
              "kind": "INPUT_REF",
              "index": 5,
              "name": "$5",
              "type": "FLOAT"
            }
          ],
          "id": "4",
          "inputs": [
            {
              "relOp": "LogicalFilter",
              "condition": {
                "kind": "AND",
                "operator": "AND",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "EQUALS",
                    "operator": "=",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 1,
                        "name": "$1",
                        "type": "VARCHAR"
                      },
                      {
                        "kind": "LITERAL",
                        "value": "LAM",
                        "type": "VARCHAR",
                        "nullable": false,
                        "precision": -1
                      }
                    ]
                  },
                  {
                    "kind": "GREATER_THAN_OR_EQUAL",
                    "operator": ">=",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "CAST",
                        "operator": "CAST",
                        "type": "INTEGER",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 4,
                            "name": "$4",
                            "type": "VARCHAR"
                          }
                        ]
                      },
                      {
                        "kind": "LITERAL",
                        "value": 201201,
                        "type": "INTEGER",
                        "nullable": false,
                        "precision": 10
                      }
                    ]
                  },
                  {
                    "kind": "LESS_THAN_OR_EQUAL",
                    "operator": "<=",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "CAST",
                        "operator": "CAST",
                        "type": "INTEGER",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 4,
                            "name": "$4",
                            "type": "VARCHAR"
                          }
                        ]
                      },
                      {
                        "kind": "LITERAL",
                        "value": 201212,
                        "type": "INTEGER",
                        "nullable": false,
                        "precision": 10
                      }
                    ]
                  }
                ]
              },
              "variableset": "[]",
              "id": "3",
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
                        "type": "INTEGER"
                      },
                      {
                        "kind": "INPUT_REF",
                        "index": 3,
                        "name": "$3",
                        "type": "INTEGER"
                      }
                    ]
                  },
                  "id": "2",
                  "inputs": [
                    {
                      "relOp": "LogicalTableScan",
                      "table": "customers",
                      "id": "0",
                      "inputs": []
                    },
                    {
                      "relOp": "LogicalTableScan",
                      "table": "yearmonth",
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