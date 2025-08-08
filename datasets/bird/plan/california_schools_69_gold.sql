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
          "name": "DESC"
        }
      ],
      "id": "3",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
            {
              "kind": "INPUT_REF",
              "index": 4,
              "name": "$4",
              "type": "VARCHAR"
            },
            {
              "kind": "INPUT_REF",
              "index": 6,
              "name": "$6",
              "type": "VARCHAR"
            }
          ],
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalFilter",
              "condition": {
                "kind": "AND",
                "operator": "AND",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "GREATER_THAN_OR_EQUAL",
                    "operator": ">=",
                    "type": "BOOLEAN",
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
                                    "index": 21,
                                    "name": "$21",
                                    "type": "DATE"
                                  }
                                ]
                              }
                            ]
                          }
                        ]
                      },
                      {
                        "kind": "LITERAL",
                        "value": "1980",
                        "type": "CHAR",
                        "nullable": false,
                        "precision": 4
                      }
                    ]
                  },
                  {
                    "kind": "LESS_THAN_OR_EQUAL",
                    "operator": "<=",
                    "type": "BOOLEAN",
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
                                    "index": 21,
                                    "name": "$21",
                                    "type": "DATE"
                                  }
                                ]
                              }
                            ]
                          }
                        ]
                      },
                      {
                        "kind": "LITERAL",
                        "value": "1989",
                        "type": "CHAR",
                        "nullable": false,
                        "precision": 4
                      }
                    ]
                  },
                  {
                    "kind": "EQUALS",
                    "operator": "=",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 3,
                        "name": "$3",
                        "type": "VARCHAR"
                      },
                      {
                        "kind": "LITERAL",
                        "value": "Closed",
                        "type": "VARCHAR",
                        "nullable": false,
                        "precision": -1
                      }
                    ]
                  },
                  {
                    "kind": "EQUALS",
                    "operator": "=",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "CAST",
                        "operator": "CAST",
                        "type": "INTEGER",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 27,
                            "name": "$27",
                            "type": "VARCHAR"
                          }
                        ]
                      },
                      {
                        "kind": "LITERAL",
                        "value": 11,
                        "type": "INTEGER",
                        "nullable": false,
                        "precision": 10
                      }
                    ]
                  }
                ]
              },
              "variableset": "[]",
              "id": "1",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "schools",
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